"""
CS 149: Parallel Computing, Assigment 4 Part 1

This file contains the kernel implementations for the vector addition benchmark.

For Step 1 & 2, you should look at these kernels:
    - vector_add_naive
    - vector_add_tiled
    - vector_add_stream
For Step 3, you should look at these kernels:
    - vector_add_direct_allocation
    - vector_add_direct_allocation_bad

It's highly recommended to carefully read the code of each kernel and understand how
they work. For NKI functions, you can refer to the NKI documentation at:
https://awsdocs-neuron-staging.readthedocs-hosted.com/en/nki_docs_2.21_beta_class/
"""

import numpy as np
import math

import neuronxcc.nki as nki
import neuronxcc.nki.language as nl
import neuronxcc.nki.isa as nisa
import neuronxcc.nki.compiler as ncc
from neuronxcc.nki import baremetal
from neuronxcc.nki import benchmark


# This Just Disables the Compiler Logging Because It Clogs the Output 
import logging
logging.disable(logging.OFF)

"""
This is the naive implementation of a vector add kernel. 
Due to the 128 partition size limit, this kernel only works for vector sizes <=128.
"""
@nki.jit
def vector_add_naive(a_vec, b_vec):
    
    # Allocate space for the output vector in HBM
    out = nl.ndarray(shape=a_vec.shape, dtype=a_vec.dtype, buffer=nl.hbm)

    # Load the input vectors
    a = nl.load(a_vec)
    b = nl.load(b_vec)

    # Add the input vectors
    res = nl.add(a, b)

    # Store the result into HBM
    nl.store(out, value=res)

    return out

"""
This is the tiled implementation of a vector add kernel.
We load the input vectors in chunks, add them, and then store the result
chunk into HBM. Therefore, this kernel works for any vector that is a
multiple of 128.
"""
@nki.jit
def vector_add_tiled(a_vec, b_vec):
    
    # Allocate space for the output vector in HBM
    out = nl.ndarray(shape=a_vec.shape, dtype=a_vec.dtype, buffer=nl.hbm)

    # Get the total number of vector rows
    M = a_vec.shape[0]
    
    # TODO: You should modify this variable for Step 1
    ROW_CHUNK = 1

    # Loop over the total number of chunks, we can use affine_range
    # because there are no loop-carried dependencies
    for m in nl.affine_range((M // ROW_CHUNK)):

        # Allocate row-chunk sized tiles for the input vectors
        a_tile = nl.ndarray((ROW_CHUNK, 1), dtype=a_vec.dtype, buffer=nl.sbuf)
        b_tile = nl.ndarray((ROW_CHUNK, 1), dtype=b_vec.dtype, buffer=nl.sbuf)
        
        # Load a chunk of rows
        a_tile[...] = nl.load(a_vec[m * ROW_CHUNK : (m + 1) * ROW_CHUNK])
        b_tile[...] = nl.load(b_vec[m * ROW_CHUNK : (m + 1) * ROW_CHUNK])

        # Add the row chunks together
        res = nl.add(a_tile, b_tile)

        # Store the result chunk into HBM
        nl.store(out[m * ROW_CHUNK : (m + 1) * ROW_CHUNK], value=res)
    
    return out

"""
This is an extension of the vector_add_tiled kernel. Instead of loading tiles
of size (ROW_CHUNK, 1), we reshape the vectors into (PARTITION_DIM, FREE_DIM)
tiles. This allows us to amortize DMA transfer overhead and load many more
elements per DMA transfer.
"""
@nki.jit
def vector_add_stream(a_vec, b_vec):

    # Get the total number of vector rows
    M = a_vec.shape[0]

    # TODO: You should modify this variable for Step 1
    FREE_DIM = 2

    # The maximum size of our Partition Dimension
    PARTITION_DIM = 128

    # The total size of each tile
    TILE_M = PARTITION_DIM * FREE_DIM

    # Reshape the the input vectors
    a_vec_re = a_vec.reshape((M // TILE_M, PARTITION_DIM, FREE_DIM))
    b_vec_re = b_vec.reshape((M // TILE_M, PARTITION_DIM, FREE_DIM))

    # Allocate space for the reshaped output vector in HBM
    out = nl.ndarray(shape=a_vec_re.shape, dtype=a_vec_re.dtype, buffer=nl.hbm)

    # Loop over the total number of tiles
    for m in nl.affine_range((M // TILE_M)):

        # Allocate space for a reshaped tile
        a_tile = nl.ndarray((PARTITION_DIM, FREE_DIM), dtype=a_vec.dtype, buffer=nl.sbuf)
        b_tile = nl.ndarray((PARTITION_DIM, FREE_DIM), dtype=a_vec.dtype, buffer=nl.sbuf)

        # Load the input tiles
        a_tile[...] = nl.load(a_vec_re[m])
        b_tile[...] = nl.load(b_vec_re[m])

        # Add the tiles together
        res = nl.add(a_tile, b_tile)

        # Store the result tile into HBM
        nl.store(out[m], value=res)

    # Reshape the output vector into its original shape
    out = out.reshape((M,))

    return out


@nki.jit
def vector_add_direct_allocation(a_vec, b_vec):
    # Create output tensor in HBM 
    out = nl.ndarray(a_vec.shape, dtype=a_vec.dtype, buffer=nl.shared_hbm)
    
    # Define constants for free dimension, physical tile count, and partition dimension
    FREE_DIM = 1000
    FREE_DIM_TILES = 4
    PARTITION_DIM = 128

    # Get the total number of vector rows
    M = a_vec.shape[0]

    # Define the size of each tile
    TILE_M = PARTITION_DIM * FREE_DIM

    # Reshape the the input vectors
    a_vec = a_vec.reshape((M // TILE_M, PARTITION_DIM, FREE_DIM))
    b_vec = b_vec.reshape((M // TILE_M, PARTITION_DIM, FREE_DIM))
    out = out.reshape((M // TILE_M, PARTITION_DIM, FREE_DIM))

    # Get the total number of tiles
    N_TILES = M // TILE_M

    # Initialize the starting byte offset for a_tensor
    current_offset = 0
    
    # Allocate space for the entire reshaped a_tensor with 4 physical tiles
    a_tile = nl.ndarray((N_TILES, nl.par_dim(PARTITION_DIM), FREE_DIM), dtype=a_vec.dtype,
            buffer=ncc.sbuf.mod_alloc(base_addr=current_offset, num_free_tiles=(FREE_DIM_TILES,)))

    # Increment the starting byte offset for b_tensor based on tile and feature size
    current_offset += FREE_DIM_TILES * FREE_DIM * 4
    
    # Allocate space for the entire reshaped b_tensor with 4 physical tiles
    b_tile = nl.ndarray((N_TILES, nl.par_dim(PARTITION_DIM), FREE_DIM), dtype=b_vec.dtype,
            buffer=ncc.sbuf.mod_alloc(base_addr=current_offset, num_free_tiles=(FREE_DIM_TILES,)))

    # Increment the starting byte offset for out_tensor, and allocate space for the entire reshaped out_tensor
    current_offset += FREE_DIM_TILES * FREE_DIM * 4
    res_tile = nl.ndarray((N_TILES, nl.par_dim(PARTITION_DIM), FREE_DIM), dtype=out.dtype,
            buffer=ncc.sbuf.mod_alloc(base_addr=current_offset, num_free_tiles=(FREE_DIM_TILES,)))

    # Loop over the total number of tiles
    for m in nl.affine_range(N_TILES):
        
        # Load the input tiles
        a_tile[m] = nl.load(a_vec[m])
        b_tile[m] = nl.load(b_vec[m])

        # Add the tiles together
        res_tile[m] = nisa.tensor_tensor(a_tile[m], b_tile[m], op=nl.add)
        
        # Store the result tile into HBM
        nl.store(out[m], value = res_tile[m])

    # Reshape the output vector into its original shape
    out = out.reshape((M, 1))

    return out

@nki.jit
def vector_add_direct_allocation_bad(a_vec, b_vec):
    out = nl.ndarray(a_vec.shape, dtype=a_vec.dtype, buffer=nl.shared_hbm)
    
    FREE_DIM = 1000
    FREE_DIM_TILES = 4
    PARTITION_DIM = 128

    M = a_vec.shape[0]

    TILE_M = PARTITION_DIM * FREE_DIM

    a_vec = a_vec.reshape((M // TILE_M, PARTITION_DIM, FREE_DIM))
    b_vec = b_vec.reshape((M // TILE_M, PARTITION_DIM, FREE_DIM))
    out = out.reshape((M // TILE_M, PARTITION_DIM, FREE_DIM))

    N_TILES = M // TILE_M

    current_offset = 0
    a_tile = nl.ndarray((N_TILES, nl.par_dim(PARTITION_DIM), FREE_DIM), dtype=a_vec.dtype,
            buffer=ncc.sbuf.mod_alloc(base_addr=current_offset, num_free_tiles=(FREE_DIM_TILES,)))

    b_tile = nl.ndarray((N_TILES, nl.par_dim(PARTITION_DIM), FREE_DIM), dtype=b_vec.dtype,
            buffer=ncc.sbuf.mod_alloc(base_addr=current_offset, num_free_tiles=(FREE_DIM_TILES,)))

    res_tile = nl.ndarray((N_TILES, nl.par_dim(PARTITION_DIM), FREE_DIM), dtype=out.dtype,
            buffer=ncc.sbuf.mod_alloc(base_addr=current_offset, num_free_tiles=(FREE_DIM_TILES,)))

    for m in nl.affine_range(N_TILES):
        a_tile[m] = nl.load(a_vec[m])
        b_tile[m] = nl.load(b_vec[m])

        res_tile[m] = nisa.tensor_tensor(a_tile[m], b_tile[m], op=nl.add)
        nl.store(out[m], value = res_tile[m])

    out = out.reshape((M, 1))

    return out

