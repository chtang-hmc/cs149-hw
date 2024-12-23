# Assignment 4: Programming a Machine Learning Accelerator #

**Due Friday Dec 6, 11:59pm**

**100 points total**

## Overview ##

In this assignment, you will learn how to implement and optimize kernels for the [AWS Trainium](https://aws.amazon.com/ai/machine-learning/trainium/) architecture, which features multiple tensor-oriented accelerated processing engines as well as software-managed on-chip storage that provides these engines high-bandwidth access to data. 

The assignment is organized into two parts.  In part 1 you will be given several implementations of a kernel that performs element-wise vector addition on Trainium. This is a simple application used to teach you about key concepts of using the Trainium architecture.  In part 2 you will implement a convolution layer for Trainium.

Overall, this assignment will:

1) Give you experience with the low-level details of tensor processing and managing on-chip SRAM on the accelerator.

2) Show the value of key locality-preserving optimizations like loop blocking and loop fusion.

## Environment Setup ##

You will be programming and testing your code on an AWS VM featuring Trainium accelerators. Please follow the instructions in [cloud_readme.md](cloud_readme.md) for setting up a machine to run the assignment.

Once you have logged in to your AWS machine, you should download the assignment starter code from the course Github using:

`git clone https://github.com/stanford-cs149/asst4-trainium`

After downloading the Assignment 4 repository, move to the `asst4-trainium` directory and **run the install script we have provided**:
```
cd asst4-trainium
source install.sh
```
The install script will activate a Python [virtual environment](https://builtin.com/data-science/python-virtual-environment) with all the needed assignment dependencies. It will also modify your `~/.bashrc` file so the virtual environment is activated upon future logins to your machine. Finally, the script sets up your InfluxDB credentials so that you may use `neuron-profile`.

## Part 0: Getting familiar with Trainium and Neuron Core Architecture

### Trainium Architecture Overview

First, let's get you acquainted with Trainium.

The `Trn1.2xlarge` instance used in this assignment features a single Trainium device, which comprises of two NeuronCores, as shown in image below. Each core is equipped with its own dedicated HBM (High-bandwidth memory). Each NeuronCore can be considered a standalone processing unit, which contains its own on-chip storage as well as a collection of specialized compute engines for performing 128x128 matrix operations (tensor engine), 128-wide vector operations (vector engine), etc. While each Trainium device has two NeuronCores, in this assignment we will be writing kernels that execute on a single NeuronCore.

<p align="center">
  <img src="https://github.com/stanford-cs149/asst4-trainium/blob/main/handout/trainium_chip.png" width=40% height=40%>
</p>

More details on the four distinct compute engines that exist in a NeuronCore can be found [here](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/general/arch/neuron-hardware/neuron-core-v2.html#neuroncores-v2-arch).

### Trainium Memory Hierarchy

In Assignment 3, one of the key concepts was learning about the GPU memory hierarchy presented by CUDA, where there was main host memory, GPU device global memory, per-thread block shared memory, and private per-CUDA-thread memory.  On Trainium, the memory hierarchy consists of four levels: **host memory (DRAM)**, **device memory (HBM)**, and two fast on-chip memory types, **SBUF (State Buffer)** and **PSUM (Partial Sum Buffer)**. These levels are shown in the figure below.

<p align="center">
  <img src="https://github.com/stanford-cs149/asst4-trainium/blob/main/handout/memory_hierarchy.png" width=80% height=80%>
</p>

* __Host memory__, is the memory address space of the host machine, and is external to the Trainium device. You can think of host memory on Trainium as similar to host memory in a CUDA programming environment.
* __HBM__ is high-bandwidth memory located on the Trainium device. Moving data from host memory to HBM requires data transfer over the machine's PCIe interconnect. HBM serves as the device's primary memory, offering large storage (32 GiB). 
* __SBUF__ is on-chip storage on the NeuronCore. In comparison, SBUF is significantly smaller than HBM (24 MiB) but offers much higher bandwidth (~20x than HBM). 
* __PSUM__ is a small, specialized memory (2 MiB) dedicated to holding matrix multiplication results produced by the tensor engine.

In Trainium, all computations require loading data from HBM into SBUF, which is accessible by all engine types. Intermediate data generated during kernel execution by the compute engines is also stored in SBUF. Once the computation is complete, the results are written back to HBM. 

<p align="center">
  <img src="https://github.com/stanford-cs149/asst4-trainium/blob/main/handout/neuron_core.png" width=40% height=40%>
</p>

Recall that in a system that features a traditional data cache, decisions about what data from off-chip memories is replicated and stored in on-chip storage are made by the cache (based on cache organization and eviction policies). Software loads data at a given memory address, and the hardware is responsible for fetching that data from memory and managing what data is stored in the cache for efficient future access. In other words, from the perspective of software correctness, the cache does not exist--it is a hardware implementation detail. 

In contrast, the memories available to a NeuronCore are *software managed*. This means that a software must explicitly move data to and from these memories using data movement commands. Either the programmer must explicitly describe data movement in their program, or the NKI compiler must analyze the application and generate the appropriate data movement operations.  Some of the biggest challenges of efficiently using the NeuronCore architecture involve efficiently orchestrating the movement of data through the machine.

## Part 1: Learning the Neuron Kernel Interface with Vector Add (30 points)

In this section, we introduce the basics of the Trainium programming model by providing several different implementations of an application that adds the elements of two vectors.

The corresponding code is organized within the `/part1` directory. Specifically, the vector addition kernels discussed here can be found in `kernels.py`. Additionally, we provide a script, `vec_add.py`, which offers a convenient command-line interface for executing these kernels with different vector sizes. The script also includes an optional flag for collecting profiling metrics.

```
usage: python vec_add.py [-h] --kernel {naive,tiled,stream,alloc,alloc_bad} --size SIZE [--profile PROFILE_NAME]

options:
  -h, --help            show this help message and exit
  --kernel {naive,tiled,stream,alloc,alloc_bad}
  --size SIZE
  --profile PROFILE_NAME
                        Save profiler metrics into .neff & .ntff files with specified name.
                        Will overwrite a .neff / .ntff file with same name if it exists.
```

### NKI Programming Model:

The Neuron Kernel Interface (NKI) is a language and compiler for developing kernels that run on Trainium devices. NKI kernels are written in Python, and make use of three types of NKI operations:
1. **Loading data** from HBM to the on-chip SBUF.
2. **Computation** executed on the NeuronCore compute engines.
3. **Storing outputs** from SBUF back to HBM.

As an example, the following kernel defines how to perform vector addition using NKI. Note that the `@nki.jit` is a Python decorator that indicates that a function should be compiled to run on NeuronDevices, much like how the `__global__` function decorator in CUDA C++ designates that a function is to be compiled as a device-side function and run on the GPU.

Similar to how arguments to CUDA kernels are arrays in CUDA device global memory, the arguments to Python functions decorated with `@nki.jit`
 are tensors that reside in HBM accessible to the NeuronCore. In the following code, `a_vec` and `b_vec` are assumed to be length 128 vectors in HBM. (The code will not work for vectors that are larger than 128. We'll explain why shortly.)
```
@nki.jit
def vector_add_naive(a_vec, b_vec):
    
    # Allocate space for the output vector in HBM
    # it is the same dimensions as a_vec, with the same element type as a_vec
    out = nl.ndarray(shape=a_vec.shape, dtype=a_vec.dtype, buffer=nl.hbm)

    # Load the input vectors from HBM into variables stored in SBUF 
    a = nl.load(a_vec)
    b = nl.load(b_vec)

    # Add the input vectors
    res = nl.add(a, b)

    # Store the result into HBM
    nl.store(out, value=res)

    return out
```

In the code above...

- `nl.load` loads the two input vectors `a_vec` and `b_vec` from HBM to SBUF. Although it's not explicit in the code that `a` and `b` are variables backed by allocations in SBUF, you know this because they are variables returned by the `nl.load` function, which returns SBUF-based values.
- `nl.add` performs the vector addition of the two vectors.
- `nl.store` stores the results back to HBM.

<p align="center">
  <img src="https://github.com/stanford-cs149/asst4-trainium/blob/main/handout/sbuf_layout.png" width=60% height=60%>
</p>

**When looking at the code above, notice that NKI operations operate on tensors, not scalar values.** Specifically, the on-chip memories, SBUF and PSUM, store data that is arranged as 2D memory arrays. The first dimension of the 2D array is called the "partition dimension" `P`. The second dimension is referred to as the "free dimension" `F`.  NeuronCores are able load and process data along the partition dimension in parallel, *but the architecture also places a restriction that the size of the partition dimension is 128 or smaller.*  In other words, 
when loading a tensor from HBM to SBUF, the partition dimension of the tensor can be at most 128.  We will talk about the restrictions of the free dimension later.

As a result, in the code above, since `a_vec` and `b_vec` are 1D vectors, their only dimension is the partition dimension, and thus their size is limited to 128 elements.  In other words the code only works for vector sizes 128 or less.

### Step 1: Chunking Vectors to Parallelize Across 128 Compute Lanes

To fix the code to work for vectors with a size greater than 128, we need to load the vectors in chunks (subsets of the original tensor). 

```
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
```

The above example breaks the vector rows into single-element chunks (the chunk size is 1 element of the vector---yes, this is inefficient, we'll come back to this in a second). This is achieved by indexing the vector using the standard Python slicing syntax `Tensor[Index:Index:...]`. More details regarding Tensor indexing in NKI can be found [here](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/general/nki/programming_model.html#nki-tensor-indexing). 

In the code above [affine_range](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/general/nki/api/generated/nki.language.affine_range.html) used here generates a sequence of numbers for loop iterators, similar to Python’s `range` function, but it requires that there are no loop-carried dependencies across iterations. Since there are no loop-carried dependencies across iterations, the NKI compiler can more aggressively optimize loop iterations to allow for increased pipelining across compute engines. In cases where loop iterations have dependencies, [sequential_range](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/general/nki/api/generated/nki.language.sequential_range.html) should be used instead.

**What you need to do:**
1. Run the above `vector_add_tiled` implementation where *row_chunk = 1* with a vector size of 25600 (*this may take a couple minutes*). You may do so using the following command:

   ```
   python vec_add.py --kernel tiled --size 25600
   ```

   What was the execution time in microseconds (μs)?

3. Remember that the maximum partition size (number of rows) that can be loaded at once on a NeuronDevice is 128. Inside `kernels.py`, change `vector_add_tiled` so that it uses *row_chunk = 128*. Record the execution time in microseconds (μs) for `vector_add_tiled` with the *row_chunk = 128* operating on a vector size of 25600. How much faster is `vector_add_tiled` on a vector size of 25600 when *row_chunk = 128* compared to when *row_chunk = 1*? Why do you think it is faster?  (Hint: you should think of the execution as loading `ROW_CHUNK` elements in parallel from HBM and then performing a `ROW_CHUNK` wide vector add on the vectors in SBUF.)
	
4. Try running `vector_add_tiled` on vector sizes of 25600 when *row_chunk = 256*. You should see an error. In one sentence, explain why you get an error when trying to run *row_chunk = 256*.

### Step 2a: Improved Data Streaming

So far, we have been able to exploit the fact that the Vector Engine can perform computation with all 128 vector lanes in parallel, with each lane streaming a single element from/to a single SBUF/PSUM partition.

However, we can improve performance further by streaming more elements across the free dimension. The Neuron Compiler is responsible for converting each NKI load/store call to Direct Memory Access (DMA) transfer. You should think of a DMA transfer as a single asynchronous operation that moves a block of data from one storage location to another -- in this case between HBM and SBUF.  

The NeuronCore has 16 DMA engines that can all work on different data transfer operations in parallel. The caveat is that there is an overhead cost when setting up a DMA transfer and assigning DMA engines to work on them. In order to reduce this setup overhead, efficient implementations should aim to move a large amount of data in each DMA transfer to amortize DMA transfer overhead.

Although the first dimension (partition dimension) of a SBUF tensor can be no greater than 128, the second dimension for a single SBUF vector instruction can be up to 64K elements. This means that it is possible to use a single instruction to load 128 * 64k = 8192k elements from HBM to SBUF. Furthermore, we can perform vector addition on two 8192k element SBUF tiles in a single arithmetic instruction. Therefore, rather than performing a `nl.load` operations (and equivalently a hardware DMA transfer request) for each 128 element chunk of a vector, we should should instead move many 128-row chunks with each DMA transfer request. This streamlined approach allows us to amortize the setup time required for transferring data.

In order to improve DMA transfer overhead, we will need to reshape our vectors so they are two-dimensional tiles, rather than linearized arrays. In Assignment 3, we worked with CUDA thread blocks partitioned across an entire image, and in order to map CUDA threads to image pixels we flattened our grid by calculating a thread’s global linear index. You can think about the reshaping process for the NeuronCore as the inverse: the goal is to turn a single-dimension vector into a dense 2D matrix. NumPy comes with a built-in [reshape function](https://numpy.org/doc/stable/reference/generated/numpy.reshape.html) allowing you to reshape arrays into the shape of your choosing. 

<p align="center">
  <img src="https://github.com/stanford-cs149/asst4-trainium/blob/main/handout/non_reshaped_DMA.png" width=48% height=48%>
  <img src="https://github.com/stanford-cs149/asst4-trainium/blob/main/handout/reshaped_DMA.png" width=48% height=48%>
</p>


Take a look at `vector_add_stream`, which extends `vector_add_tiled` so that there are less DMA transfers:
```
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
```

**What you need to do:**
1. Run the above `vector_add_stream` implementation where *FREE_DIM = 2*. How many microseconds (μs) did it take to run for a vector size of 25600? How much faster is this compared to `vector_add_tiled` with *row_chunk = 128* from Step 1?
2. The current `vector_add_stream` implementation reduces the number of DMA transfers slightly, but the number of DMA transfers can be reduced further. Inside `kernels.py`, change the value of *FREE_DIM* for `vector_add_stream` to reduce the number of DMA transfers as much as possible on a vector of size 25600.

   What value of *FREE_DIM* did you choose? What was the execution time in microseconds (μs) on a vector size of 25600 for this value of *FREE_DIM*. How much faster is `vector_add_stream` with the *FREE_DIM* number you chose than `vector_add_stream` with *FREE_DIM = 2*? How much faster is `vector_add_stream` with the *FREE_DIM* number you chose than `vector_add_tiled` with *row_chunk = 128*?

### Step 2b: Learning to Use Neuron-Profile

There is a trade-off in choosing a tile's free dimension size:
1. Too small of a tile size exposes significant instruction overhead leading to inefficient engine execution.
2. Too large of a tile size often leads to inefficient pipelining between engines and high memory pressure in SBUF in cases of data reuse ("memory pressure" means that SBUF may fill up).

Currently, we have explored the benefits of increasing tile sizes to their maximum amount in order for us to amortize instruction overhead and DMA transfer setup / teardown. Now, we will explore why making the free dimension as large as possible is not always the best solution.

For this task, you will need to use the profiling tool for NeuronDevices: `neuron-profile`, which can provide detailed analysis of the performance of an application running on a NeuronCore. In order to run the profiling tool, you must make sure that you ran the install script as detailed in [Environment Setup](https://github.com/stanford-cs149/asst4-trainium/tree/main?tab=readme-ov-file#environment-setup) and that you forwarded ports 3001 and 8086 when you ssh'd into your machine. To reiterate on the latter, the command you should have ran is:

 `ssh -i path/to/key_name.pem ubuntu@<public_dns_name> -L 3001:localhost:3001 -L 8086:localhost:8086`
 
 More details about why this is needed can be found in the [cloud_readme.md](https://github.com/stanford-cs149/asst4-trainium/blob/main/cloud_readme.md).

**What you need to do:**
1.  This time, we are going to increase the vector sizes by a factor of 10 so that instead of adding 25600 elements we will be adding 256000 elements. This will allow us to see trade offs that comes from dealing with tile sizes that are too large.  

    First, set *FREE_DIM = 2000* in `vector_add_stream`. Now, just like the prior steps we are going to execute our kernel, but this time we are going to save the compiled kernel 
    into a **.neff** file and the kernel execution trace into a **.ntff** trace file. Let's run `vector_add_stream` on a vector_size of 256000 and save the compiled kernel and 
    trace into files prefixed with `stream_256k_fd2k` with the following command:

    ```
    python vec_add.py --kernel stream --size 256000 --profile stream_256k_fd2k
    ```

    You should have generated two files: ***stream_256k_fd2k.neff*** and ***stream_256k_fd2k.ntff***.
    
    Now, using a similar workflow run `vector_add_stream` with *FREE_DIM = 1000* on a vector_size of 256000 and save the compiled kernel and trace into files prefixed with 
    `stream_256k_fd1k`.
2.  These generated files will allow us to collect kernel execution metrics using the `neuron-profile` tool. These profiling metrics will be very useful for analyzing the 
    performance of your kernels. Let's look at a brief summary of execution metrics for `vector_add_stream` with *FREE_DIM = 2000* by running the following command:

    ```
    neuron-profile view --output-format summary-text -n stream_256k_fd2k.neff -s stream_256k_fd2k.ntff
    ```

    You will see a summarized output consisting of various execution metrics in alphabetical order. Let's look at two specific metrics: 
    
     * **dma_transfer_count**: The number of DMA transfers
     * **total_time**: Kernel execution time in seconds

    What was kernel execution time in seconds when *FREE_DIM = 2000*? How many DMA transfers were made when *FREE_DIM = 2000*?
    
    Using the same workflow as before, look at the summary of execution metrics when *FREE_DIM = 1000*.
    
    What was kernel execution time in seconds when *FREE_DIM = 1000*? How many DMA transfers were made when *FREE_DIM = 1000*?

3. Although the kernel with *FREE_DIM = 1000* had more DMA transfers, it was faster! Let's analyze why.
  
   We can dive deeper into the kernel execution metrics using the GUI functionality of `neuron-profile`. Let's launch the GUI for `vector_add_stream` with *FREE_DIM = 2000* by 
   running the following command:

   ```
   neuron-profile view -n stream_256k_fd2k.neff -s stream_256k_fd2k.ntff
   ```

   After running the command, you will see an output like the following:

   `View profile at http://localhost:3001/profile/...`

   Paste this *http* link into a browser of your choice to view more in-depth profiler analytics. **Note:** You will only be able to view this if you have correctly forwarded 
   ports 3001 and 8086 when you ssh'd into your machine.

   ![Profiler GUI Example](https://github.com/stanford-cs149/asst4-trainium/blob/main/handout/profiler_GUI.gif)

   Hover over various events in the graph generated from the profiler. See the above GIF as an example for `vector_add_stream` with *FREE_DIM = 2000* on a vector size of 256000. 
   In the example we see the following:
   
   * **qGpSimdIO0**: Shows DMA transfer for storing the output vector
   * **qSyncIO0**: Shows DMA transfers for loading the two input vectors
   * **VectorE**: Shows the Vector Engine instruction for add the two input vectors
   * **Pending DMA Count**: Shows the number of pending DMA transfers over time
   * **DMA Throughput**: Shows the device bandwidth over time

   Now, in your terminal press `ctrl-c` to exit the current `neuron-profile view`. Note that you can still view the GUI analytics for `vector_add_stream` with *FREE_DIM = 2000* in 
   your browser as they have been temporally stored in a database. Following the same workflow, launch the GUI analytics for `vector_add_stream` with *FREE_DIM = 1000*.

4. After analyzing the GUI analytics graph for `vector_add_stream` with both *FREE_DIM = 2000* and *FREE_DIM = 1000*, briefly explain why FREE_DIM = 1000 has a faster execution 
   time than FREE_DIM = 2000 even though it required more DMA transfers (*Hint:* pipelining).

   Feel free to also play around with various functionalities in the `neuron-profile` GUI. Some particularly useful things are in the `View Settings` tab located at the bottom 
   toolbar. In the `View Settings` tab you can toggle `Group DMA by engine` and `Show expanded DMA` to have the graph show the DMA transfers from each of the NeuronCore's 16 DMA 
   engines. You may also want to look at the `Summary` tab located at the bottom toolbar. This tab displays the same brief summary of execution metrics we saw when running  
   `neuron-profile view --output-format summary-text ...` in Question 2. Feel free to learn more about `neuron-profile` functionality from the [user guide](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/tools/neuron-sys-tools/neuron-profile-user-guide.html) and interesting performance metrics for NKI kernels from the [NKI performance guide](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/general/nki/nki_perf_guide.html).

### Step 3: Direct Allocation

As discussed above, a multi-dimensional NKI tensor requires one of its dimensions to be mapped to the partition dimension (`P`)  of physical memory. We also define an NKI Tile as any NKI Tensor with the first dimension being the partition dimension. 

To introduce NKI allocation API, let’s define the block (`B`) dimension as any dimension before the partition dimension in a NKI Tensor. Therefore, a NKI Tensor has three types of dimensions: `(B, P, F)`. Note, a NKI Tensor can have one or more dimensions in both `B` and `F`, but there can only be one dimension in `P` due to Neuron ISA requirements. The block dimension effectively describes how many `(P, F)` NKI Tiles the tensor has, which commonly corresponds to how many NKI API compute API invocations we need to process the entire tensor.

In fact, the block dimensions are considered **logical** in NKI - the number of `(P, F)` NKI Tiles suggested by dimension (`B`) may not match the number of physical tiles allocated. The Neuron Compiler analyzes the code and uses a heuristic-driven allocator to determine the number of physical tiles allocated to each tensor. Given the amount of parallelism available in hardware and the complex parallel programs seen in common machine learning workloads, the heuristic-based memory allocator in Neuron Compiler may not yield the optimal allocation decisions. Bad allocation decisions typically lead to sub-optimal engine parallelism and/or on-chip memory over-subscription causing excessive spills of intermediate data to device memory. 

For example, consider a scenario where the block dimension size of `nki_tensor` is 2, yet only a single physical tile, `T`, is allocated in `sbuf`.
```
   i_block = 0
1. nl.load(nki_tensor[0, :, :]) => write T
2. nl.exp(nki_tensor[0, :, :])  => read T

   i_block = 1
3. nl.load(nki_tensor[1, :, :]) => write T
4. nl.exp(nki_tensor[1, :, :])  => read T
```

In this example, there is only one logical tile in `nki_tensor` that is alive at one time. However, in NeuronCore, `nl.load` and `nl.exp` are executed using two independent resources: DMA Engine and Scalar Engine. In this serialized execution, there is no instruction parallelism achieved between these engines. There is potential for performance optimization by allocating more physical tiles.

With [NKI direct allocation API](https://awsdocs-neuron-staging.readthedocs-hosted.com/_/sharing/PsLqSocIDcWDsjcHd3ql7JCqOhou3AKO?next=/en/nki_docs_2.21_beta_class/general/nki/nki_direct_allocation_guide.html#nki-direct-allocation-guide), programmers can bypass the compiler allocator and take full control of memory allocation in SBUF/PSUM for NKI Tensors. NKI provides two kinds of direct allocation API, `nisa.sbuf.alloc()` and `nisa.sbuf_mod_alloc()`. The first one requires programmers to define an allocation algorithm from scratch, while the second one invokes a pre-defined modulo allocation scheme in Neuron Compiler. In this tutorial, we will focus on the `nisa.sbuf.mod_alloc()` API, which is already powerful enough.
Modulo allocation works as follows. Suppose that we allocate two physical tiles for a tensor with a logical shape of `(8, par_dim(128), 512)`. The eight logical tiles are assigned to the two physical tiles by taking a modulo of two on the logical tile index. Therefore, logical tiles with index (0, ), (2, ), (4, ), (6, )share the same physical tile, while logical tiles (1, ), (3, ), (5, ), (7, ) share the other physical tile.  

The `nisa.sbuf.mod_alloc` API takes four input parameters:
1. `base_addr` indicates the starting byte offset within each SBUF partition of the physical tiles.
2. `base_partition` indicates the starting SBUF partition of the physical tiles.
3. `num_par_tiles` indicates the number of physical tiles to be allocated along the partition dimension of SBUF. This is only applicable for tiles that use fewer than 64 partitions per ISA constraints.
4. `num_free_tiles` indicates the number of physical tiles to be allocated along the free dimension of SBUF.

Take a look at `vector_add_direct_allocation`, which uses `nisa.sbuf.mod_alloc` to allocate physical tiles for input and output tensors.

```
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

    # Increment the starting byte offset, and allocate space for the entire reshaped out_tensor
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
```

**What you need to do:**
1. Run the above `vector_add_direct_allocation` implementation on a vector size of 256000 and record the execution time in microseconds (μs). Now run `vector_add_stream` from Step 2 with *FREE_DIM=1000* on a vector size of 256000 and record the execution time in microseconds (μs).
   
    You should notice that the execution times are the same. This is because `vector_add_direct_allocation` manually performs the allocations that the Neuron compiler 
    automatically performs for `vector_add_stream`. Having the ability to perform manual allocations does not yield much benefit when performing vector addition as there is no 
    data reuse and no need for engine paralelism. However, in other scenarios you will find that having more fine-grained control of memory allocations allows you to keep data on- 
    chip for as long as possible, as well as giving you the ability to perform different computations in parallel across multiple engines.
2. How many physical tiles are allocated for each tensor? What is the problem if we set the number of physical tiles too large?
3. Why should we have different offsets for each tensor? Try to run `vector_add_direct_allocation_bad` on a vector size of 256000. What is the result? Please provide a possible explanation.

## Part 2: Implementing a Fused Convolution - Max Pool Layer (70 points)

Now that you’ve learned how to efficiently move data on a NeuronCore, it is time to program an actual Trainium kernel yourself. In this section, your task is to implement a kernel that performs both convolution and an operation called "max pooling" As we discussed in class, these two operations are a fundamental component of modern Convolutional Neural Networks (CNNs), which are extensively used for computer vision tasks. An important detail is that your implementation of these two operations will be "fused", mean you will implement the computation on Trainium without dumping intermediate values to off-chip HBM. 

### Matrix Operations on a NeuronCore

Before you begin, we will demonstrate how to perform matrix operations on a NeuronCore. As discussed earlier, a NeuronCore is equipped with various compute engines, each optimized for specific types of arithmetic operations. In Part 1, you worked with the Vector Engine, which specializes in vector operations like vector addition. However in Part 2, you not only need to perform vector operations, but you will also need to perform matrix operations. The Tensor Engine on Trainium is specifically designed to accelerate these matrix operations, such as matrix multiplication and matrix transpose. 

<p align="center">
  <img src="https://github.com/stanford-cs149/asst4-trainium/blob/main/handout/tensor_engine.png" width=60% height=60%>
</p>

The above image depicts the architecture of the Tensor Engine. The Tensor Engine is built around a 128x128 [systolic processing array](https://gfxcourses.stanford.edu/cs149/fall24/lecture/hwprog/slide_10) which streams matrix data input from SBUF (on-chip storage) and writes the output to PSUM (also on-chip storage). Like SBUF, PSUM is fast on-chip memory, however it is much smaller than SBUF (2MiB vs 24 MiB) and serves a dedicated purpose of storing matrix multiplication results computed by the Tensor Engine. The Tensor Engine is able to read-add-write to every address in PSUM. Therefore, PSUM is useful when executing large matrix multiplications in a tiled manner, where the results of each matrix multiply are accumulated into the same output tile. 

Recall that the Vector Engine has the capability to operate on SBUF tiles of size (128, 64k). However, the Tensor Engine contains unique SBUF tile size constraints which differ to that of the Vector Engine. Suppose we want the Tensor Engine to perform the matrix multiplication C = A x B, where A and B are located in SBUF, and the result C is stored in PSUM. Trainium imposes the following constraints. 
  - Matrix A - the left-hand side tile - can be no bigger than (128, 128)
  - Matrix B - the right-hand side tile - can be no bigger than (128, 512).
  - The output tile C in PSUM is restricted to a size of (128, 512).

### An NKI Matrix Multiplication Kernel

Given the constraints of the Tensor Engine, implementing matrix multiplication for arbitrary matrix dimensions on Trainium requires tiling the computation so it is performed as a sequence of matrix multiplications on fixed-size tiles. (This is similar to how vector addition in part 1 was tiled to work for large input vector sizes). The example below, which we copied from the [NKI tutorials](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/general/nki/tutorials.html), demonstrates how to implement matrix multiplication using a tiled approach, where the tiles are sized to meet the Trainium Tensor engines tile size constraints. Note: a description of the code is provided after the code listing.

```
def nki_matmul_tiled_(lhsT, rhs, result):
  """NKI kernel to compute a matrix multiplication operation in a tiled manner"""

  K, M = lhsT.shape
  K_, N = rhs.shape
  assert K == K_, "lhsT and rhs must have the same contraction dimension"

  # Maximum free dimension of the stationary operand of general matrix multiplication on tensor engine
  TILE_M = nl.tile_size.gemm_stationary_fmax  # 128

  # Maximum partition dimension of a tile
  TILE_K = nl.tile_size.pmax  # 128

  # Maximum free dimension of the moving operand of general matrix multiplication on tensor engine
  TILE_N = nl.tile_size.gemm_moving_fmax  # 512

  # Use affine_range to loop over tiles
  for m in nl.affine_range(M // TILE_M):
    for n in nl.affine_range(N // TILE_N):
      # Allocate a tensor in PSUM
      res_psum = nl.zeros((TILE_M, TILE_N), nl.float32, buffer=nl.psum)

      for k in nl.affine_range(K // TILE_K):
        # Declare the tiles on SBUF
        lhsT_tile = nl.ndarray((TILE_K, TILE_M), dtype=lhsT.dtype, buffer=nl.sbuf)
        rhs_tile = nl.ndarray((TILE_K, TILE_N), dtype=rhs.dtype, buffer=nl.sbuf)

        # Load tiles from lhsT and rhs
        lhsT_tile[...] = nl.load(lhsT[k * TILE_K:(k + 1) * TILE_K,
                                      m * TILE_M:(m + 1) * TILE_M])
        rhs_tile[...] = nl.load(rhs[k * TILE_K:(k + 1) * TILE_K,
                                    n * TILE_N:(n + 1) * TILE_N])

        # Accumulate partial-sums into PSUM
        res_psum += nl.matmul(lhsT_tile[...], rhs_tile[...], transpose_x=True)

      # Copy the result from PSUM back to SBUF, and cast to expected output data-type
      res_sb = nl.copy(res_psum, dtype=result.dtype)
      nl.store(result[m * TILE_M:(m + 1) * TILE_M, n * TILE_N:(n + 1) * TILE_N],
               value=res_sb)
```

Let's break down the components of the kernel which computes the matrix multiply: `result = lst x rhs`.

  - Input Tensors:
      - `lhsT` is the left-hand-side matrix. But the matrix is provided in a __transposed format__ with shape `[K,M]`, where both `K` and `M` are multiples of 128.
      - `rhs` is the right-hand-side matrix, with shape `[K,N]`, where `K` is a multiple of 128, and `N` is a multiple of 512.
      - `result` is the output matrix of shape `[M,N]`
      - In matrix multiplication, the **contraction dimension** refers to the column dimension of the left-hand matrix and the row dimension of the right-hand matrix. For example, say 
        we have the following matrix multiplication: `A x B = C`. The matrix `A` has shape  
        `[M, N]` and the matrix `B` has shape `[N, M]`. The shape of `C` is then `[M, M]`. 
        Thus, the dimensions that were eliminated was the column dimension of `A` and the row dimension of `B`. Please note that in the `nki_matmul_tiled_` example above, the 
        matrix is in transposed form and is transposed again in the `nl.matmul` call. Thus, the matrix multiplication which is being performed is  
        `A^T x B = C`. In the transposed case, the 
        contraction dimension is the row dimension of `A` and the row dimension of `B` due to the fact that `A` is transposed before multiplication.
  - Tile Dimensions:
      - The tile sizes are set based on the constraints of the tensor engine matrix multiplication operation, as described [here](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/general/nki/api/generated/nki.isa.nc_matmul.html).
        - `TILE_M`: 128 — Tile size for the `M` dimension.
        - `TILE_K`: 128 — Tile size for the `K` dimension.
        - `TILE_N`: 512 — Tile size for the `N` dimension.
  - Looping Over Tiles:
      - The kernel uses `affine_range` loops to iterate over tiles along the `M` and `N` dimensions of the `result` matrix.
      - For each output tile of shape `(TILE_M, TILE_N)`, it allocates a temporary partial sum tensor `res_psum` in PSUM memory.
  - Loading Tiles:
      - For each output tile, tiles of `lhsT` and `rhs` are loaded into the on-chip SBUF memory for efficient access.
      - `lhsT_tile` is loaded with a slice of shape `[TILE_K, TILE_M]`, and `rhs_tile` is loaded with a slice of shape `[TILE_K, TILE_N]`.
  - Matrix Multiplication:
      - A partial matrix multiplication is performed using the loaded tiles and partial results are accumulated into `res_psum`.
  - Storing Results:
      - Once the tiles for a given result block are fully computed, the partial sums in `res_psum` are copied to SBUF and cast to the required data type.
      - The final result is stored back into the `result` tensor at the corresponding position.

In summary, this tiled implementation handles large matrix dimensions by breaking them into hardware-compatible tile sizes. It leverages specialized memory buffers (i.e., PSUM) to minimize memory latency and optimize matrix multiplication performance. You can read more about NKI matrix multiplication [here](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/general/nki/tutorials/matrix_multiplication.html).

### Convolution Layer Overview

Let’s now turn our focus to the convolution layer. Recall the [convolution operation](https://gfxcourses.stanford.edu/cs149/fall24content/media/dnneval/10_dnneval.pdf) discussed in class. It involves sliding a filter across an __input feature map__, where at each position the filter interacts with the overlapping input region. In each overlapping region, element-wise multiplications are performed between the filter weights and the input region region values. The results of these element-wise multiplications are then added together, producing a single value for the corresponding position in the output feature map. This process captures local spatial patterns and relationships among neighboring features.

<p align="center">
  <img src="https://github.com/stanford-cs149/asst4-trainium/blob/main/handout/convolution.png" width=55% height=55%>
</p>

The input feature map often consists of multiple channels. For example, an image usually contains three RGB channels (red, green, and blue). In this case, instead of only computing a weighted sum over the 2D spatial region, the convolution computes the weighted sum of both the 2D spatial region and channel depth. The image below depicts an example of a convolution layer being performed on a 32x32 input image with three RGB channels. In the image, a 5x5x3 filter is applied on the 32x32x3 image to produce a 28x28x1 output feature map.

<p align="center">
  <img src="https://github.com/stanford-cs149/asst4-trainium/blob/main/handout/cs231n_convolution.png" width=55% height=55%>
  <br>
  <em>Source: CS231N https://cs231n.stanford.edu/slides/2024/lecture_5.pdf </em>
</p>

__As seen in the image, each filter produces a single channel of output.__ To generate multiple output channels, multiple filters are applied to the input featuer map. In addition to this, each convolution filter also contains a scalar bias value that is to be added to each weighted sum. 

The input and output of the convolution operator can be summarized as follows (ignoring bias for now):

<p align="left">
  <img src="https://github.com/stanford-cs149/asst4-trainium/blob/main/handout/conv2d_summary.png" width=50% height=50%>
</p>

Additionally, a [convolution layer](https://pytorch.org/docs/stable/generated/torch.nn.functional.conv2d.html) can take in additional hyper-parameters such as padding and stride in addition to an input feature map, filter weights, and scalar bias. However, we have *simplified the constraints of your convolution* to make implementation easier for you. You need **only to support a stride of 1**, and you do **not have to worry about padding** as we will pad the input feature map for you before it is passed into your kernel.

### Mapping Convolution to Matrix Multiplication

Now, our objective is to map the convolution operator onto thee high-performance matrix operations supported by the Trainium's Tensor engine.  To do this, we can compare the mathematical formulation of convolution with matrix multiplication.

**Conv2D:**

<p align="center">
  <img src="https://github.com/stanford-cs149/asst4-trainium/blob/main/handout/conv2d_formula.png" width=65% height=65%>
</p>

**Matrix Multiplication:**

<p align="center">
  <img src="https://github.com/stanford-cs149/asst4-trainium/blob/main/handout/matmul_formula.png" width=25% height=25%>
</p>

In class we discussed one way to convert convolution with many filters into a single large matrix multiplication.  We'll do the same thing here, but take a different approach that yields an efficient implementation on Trainium.  In this approach the convolution operation is formulated as a series of independent matrix multiplications. A visual illustration of this formulation is shown below.

<p align="center">
  <img src="https://github.com/stanford-cs149/asst4-trainium/blob/main/handout/conv2d_matmul_diagram.png" width=100% height=100%>
</p>

In this approach, the height and width dimensions of the input feature map are flattened into a single dimension, reshaping the input to `(Height × Width) × Input Channels​`. This reshaped input is then multiplied by each position of the filters, where `i` and `j` respectively range from `0` to `Filter Height - 1` and from `0` to `Filter Width - 1`. Each filter slice has a shape of `Input Channels × Output Channels`, and the resulting matrix multiplication contracts along the `Input Channels` dimension. To align the input with each filter slice, the input must be shifted by an offset corresponding to the filter’s current position `(i, j)`. The results of these matrix multiplications are accumulated to produce the output tensor.

Below is the pseudocode for the described algorithm:
```
- Have the input image with shape (Input Channels, Image Height * Image Width)
- Have the filter weights with shape (Filter Height, Filter Weight, Input Channels, Output Channels)
- Initialize the output to appropriate shape of (Output Channels, Output Height * Output Width)

# Iterate over the filter height
for i in range(Filter_Height):
    # Iterate over the filter width
    for j in range(Filter_Width):

        # Shift the Input tensor by (i, j) to align with the filter's current position
        input_shifted = shift(input, (i, j))

        # Perform matrix multiplication between the input and the filter slice
        output += matmul(transpose(weight[i,j,:,:]), input_shifted)
```

> Do note that this just an algorithmic description, and the purpose of this assignment is for you to figure out to map this algorithmic description to an efficient implementation on this hardware!

### Max Pool Layer Overview
Max pooling layers are commonly used in CNNs between successive convolutional layers to reduce the size of the feature maps. Not only does this prevent excessively large feature maps which can pose a problem for computational resources, but it also reduces the amount of parameters in the CNN which effectively reduces model overfitting.

A max pooling layer operates similarly to a convolution layer in that it slides a filter spatially over an input feature map. However, instead of computing a weighted sum for each overlapping region, the max pooling layer selects the maximum value from each region and stores it in the output feature map. This operation is applied independently to each channel of the feature map, thus the number of channels remains unchanged. For instance, consider a 4x4 input image with three RGB channels passing through a max pooling layer with a 2x2 filter. The resulting output is a 2x2 image with three RGB channels, showing that the spatial dimensions are reduced by a factor of 2 while the number of channels remains the same.

<p align="center">
  <img src="https://github.com/stanford-cs149/asst4-trainium/blob/main/handout/maxpool.png" width=37% height=37%>
</p>

As shown above, a [max pool layer](https://pytorch.org/docs/stable/generated/torch.nn.functional.max_pool2d.html#torch.nn.functional.max_pool2d) typically has separate stride and filter size hyperparameters. Similar to the convolution layer, we have simplified the constraints for the max pooling layer you are required to implement. Instead of defining both parameters, your kernel will use a single parameter, `pool_size`, which corresponds to both the filter size and the stride. The `pool_size` can only be set to either 1 or 2. When `pool_size` is 2, the max pooling operation behaves as shown in the image above. When `pool_size` is 1, the max pooling layer functions as a no-op, producing an output identical to the input. While a `pool_size` of 1 might seem pointless, it actually offers added flexibility for your fused layer, as you will soon see. 

### Fusing Convolution and Max Pool
You will implement an NKI kernel that combines the Convolution and Max Pool layers into a single, fused operation. Below, we will outline the detailed specifications and requirements for your fused layer.

<p align="center">
  <img src="https://github.com/stanford-cs149/asst4-trainium/blob/main/handout/fused_kernel.png" width=95% height=95%>
</p>

The diagram above illustrates the calculations your fused kernel would perform on a 6x6 input with a single input channel. The fused kernel performs a standard convolution with one filter and stride of 1. The fused kernel then performs a max pool on the convolution result using a 2x2 pooling filter.

Your fused kernel takes in the following parameters:
  - `X` - A batch of input images. `X` has shape `(Batch Size, Input Channels, Input Height, Input Width)`. You are guaranteed that `Input Channels` will be a multiple of 128.
  - `W` - The convolution filter weights. `W` has shape `(Output Channels, Input Channels, Filter Height, Filter Width)`. You are guaranteed that `Filter Height == Filter Width`. You are also guaranteed that `Output Channels` is a multiple of 128. Moreover, you can assume that the size of the weights would always be such that it can completely fit inside SBUF.
  - `bias` - The convolution filter biases. `bias` has shape `(Output Channels)`
  - `pool_size` - The size of the max pooling filter and pooling stride. You are guaranteed that the size of the input, the size of the filter, and the `pool_size` would be such that everything is nicely divisible. More concretely, `(Input Height - Filter Height + 1) % Pool Size == 0`.  Notice that if the value of `pool_size` is `1`, then the fused kernel operates as a normal convolution kernel. This gives us the flexibility to choose whether we want max pooling or not.

Feel free to use the [course slides](https://gfxcourses.stanford.edu/cs149/fall24/lecture/dnneval/slide_43) on a convolution layer implementation as a starting point. If you are referencing the course slides, `INPUT_DEPTH` is synonymous with `Input Channels` and `LAYER_NUM_FILTERS` is synonymous with `Output Channels` in our naming scheme. Note that the input parameters to your fused kernel have different shapes than depicted in the convolution course slides. You are free to reshape the inputs into whatever shapes you desire by using the [NumPy reshape method](https://numpy.org/doc/stable/reference/generated/numpy.reshape.html) just as was done in `vector_add_stream kernel` from Part 1. We have also given you the NumPy implementations of a convolution layer and a maxpool layer in `part2/conv2d_numpy.py`. The NumPy implementations should give you a general outline of the programming logic for each layer. It might be a good exercise to think about how you would be able to fuse the NumPy implementations into a single layer, which is what you will do in your kernel. Feel free to look over the [NKI tutorials](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/general/nki/tutorials.html) to learn more about additional optimizations or other API functions. You can also view the [API Reference Manual](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/general/nki/api/index.html) to see all of the API functions that are available and their usage. You may find some of them useful. For example, [nl.max](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/general/nki/api/generated/nki.language.max.html#nki.language.max) should be helpful for max pooling.

### What You Need To Do
For this part of the assignment, focus exclusively on the file `part2/conv2d.py`. We've provided basic starter code; your task is to complete the implementation of the (fused) Conv2D kernel within the function `fused_conv2d_maxpool`.

Prioritize Correctness First. Before optimizing for performance, ensure your implementation is correct. While you may choose to implement things differently, we recommend starting by creating a kernel that works for small image sizes. Then, once your kernel works for small images, extend its functionality to handle images that are too large to fit entirely in the SBUF buffer. Following that, incorporate bias addition. Proceed to optimize performance once you achieve correctness. The test harness is written such that you can optimize your conv2D implementation without having to fuse a maxpool operation with it. Thus, you may choose to fuse a max pool operation with the conv2d kernel after you have an implementation which is correct and meets the performance requirements.

Use the test harness script provided to validate your implementation. To run the tests, navigate to the `part2/` directory and execute:
```
python3 test_harness.py
```

To check the correctness and performance of your implementation of Conv2D kernel with a fused maxpool, invoke the test harness with the `--test_maxpool` flag. To debug your implementations, run the test harness with the `--simulate` flag. This wraps your implementation with a call to `nki.simulate_kernel()`: you can read more about it [here](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/general/nki/api/generated/nki.simulate_kernel.html#nki.simulate_kernel). When running in simulation mode, you can insert calls to `nki.device_print()` to print intermediate values of the device tensors. This can help identify potential bugs.

The test harness will run correctness tests first, and run performance checks next. A full-credit solution must achieve performance within 150% of the reference kernel while maintaining correctness. It will invoke your kernel with input tensors having data types float32 and float16: with the performance requirements for float16 being more strict. Make sure you write your kernels keeping this in mind!

Students also need to submit a write up briefly describing their implementations. Also describe how you went about optimizing your implementation. Make sure to profile your implementation, and report the achieved MFU, with both `bfloat16` and `float32` data types. You can so by running `neuron-profile view`. Run the test harness with the `--profile <profile_name>` flag to capture a trace.

## Grading Guidelines

For the correctness test, we use two types of images. The first type is a small image with dimensions of 32×16. The second type is a large image with dimensions of 224×224, which exceeds the capacity of the SBUF and cannot fit within it.

For the performance test, we evaluate the performance under different configurations: with and without maxpool, and using float16 versus float32 precision. We will compare the performance of your program with the reference solution. You will pass the test if your p99 latency is within 150% of the reference latency.

**Write Up: 40 Points**
  - Part 1 Questions: 30 Points
  - Part 2 Questions: 10 Points

**Correctness of Fused Convolution - MaxPool Kernel: 45 Points + 5 Points EC**
  - With Small Images: 15 points (Full credit if only works with `--simulate`)
  - With Large Images: 15 points (Full credit if only works with `--simulate`)
  - With Bias Addition: 15 points (Full credit if only works with `--simulate`)
  - With Small Images, Large Images, and Bias Addition Tests not running with `--simulate`: 2.5 points EC
  - With Max Pool: 2.5 points EC (Must run without `--simulate`)

**Performance of Fused Convolution - MaxPool Kernel on Large Images: 15 Points + 5 Points EC**
  - Without Max Pool (Float 16): 7.5 points
  - Without Max Pool (Float 32): 7.5 points
  - With Max Pool (Float 16): 2.5 points EC
  - With Max Pool (Float 32): 2.5 points EC

## Hand-in Instructions

Please submit your work using Gradescope. If you are working with a partner please remember to tag your partner on gradescope.

1. **Please submit your writeup as the file `writeup.pdf`.**
2. **Please submit `conv2d.py` from part 2.**
