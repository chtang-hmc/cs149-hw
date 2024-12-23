"""
CS 149: Parallel Computing, Assigment 4 Part 1

This script benchmarks various vector addition kernels using different optimization strategies.
It supports profiling and saving results in .neff and .ntff formats.

For Part 1, your task is to run this script, benchmarking the kernels, and reason about the results.
"""

import argparse
from argparse import RawTextHelpFormatter
import numpy as np
from kernels import * # check kernels.py for the kernel implementations
import io
import sys
import subprocess

def save_trace(profile_name):
    """Run the profiler and save the NEFF and NTFF files with the specified name."""
    subprocess.run(["neuron-profile", "capture", "-n", "file.neff", "-s", profile_name + ".ntff"],
        check=True)

    subprocess.run(["mv", "file.neff", profile_name + ".neff"], check=True)

    print(f"\n\nNEFF / NTFF files generated with names: {profile_name + '.neff'}, {profile_name + '.ntff'}")

def benchmark_vector_add(kernel, a, b, kernel_name, profile_name, disable_allocation_opt):
    """
    Benchmark a vector addition kernel function and verify its correctness.

    This function runs a specified vector addition kernel on input arrays `a` and `b`, 
    measures its performance, and optionally saves profiling data. It compares the 
    kernel's output with the expected result from a standard NumPy operation to ensure 
    correctness.

    Parameters:
    -----------
    kernel : function
        The kernel function that performs vector addition.
    a, b : numpy.ndarray
        Input arrays for the vector addition operation.
    kernel_name : str
        Label identifying the kernel (e.g., "naive", "tiled").
    profile_name : str or None
        Name for saving profiling files; if None, profiling is not saved.
    disable_allocation_opt : bool
        Disables allocation optimizations to ensure the kernel runs as intended, 
        without compiler modifications.

    Returns:
    --------
    None
        This function prints the benchmark results and correctness validation to stdout.

    Raises:
    -------
    AssertionError
        If the kernel output does not match the expected NumPy result.
    """
    # run without benchmarking to verify correctness
    out = nki.baremetal(kernel, disable_allocation_opt=disable_allocation_opt)(a, b) 
    out = out.reshape(a.shape)
    out_np = a + b # expected result by NumPy
    
    print(f"Corectness passed? {np.allclose(out, out_np)}")
    assert np.allclose(out, out_np) # check correctness
    
    # run with benchmarking
    bench_func = None

    if profile_name is not None:
        bench_func = nki.benchmark(warmup=1, iters=10, save_neff_name='file.neff', disable_allocation_opt=disable_allocation_opt)(kernel)
    else:
        bench_func = nki.benchmark(warmup=1, iters=10, disable_allocation_opt=disable_allocation_opt)(kernel)
    

    print("\nReading NEFF File.........")
    text_trap = io.StringIO()
    sys.stdout = text_trap
    bench_func(a, b)
    sys.stdout = sys.__stdout__
    p99_us = bench_func.benchmark_result.nc_latency.get_latency_percentile(99) # Get 99th percentile latency
    print(f"\n\nExecution Time: {p99_us} μs")
    
    if profile_name is not None: save_trace(profile_name)

def main():
    parser = argparse.ArgumentParser(formatter_class=RawTextHelpFormatter)
    parser.add_argument("--kernel", type=str, choices=["naive", "tiled", "stream", "alloc", "alloc_bad"], required=True)
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--profile", dest='profile_name', 
    help='''Save profiler metrics into .neff & .ntff files with specified name.\nWill overwrite a .neff / .ntff file with same name if it exists.''')

    args = parser.parse_args()

    # Generate random input arrays
    a = np.random.rand(args.size).astype(np.float32)
    b = np.random.rand(args.size).astype(np.float32)
    
    print(f"\nRunning {args.kernel} with vector size = {args.size}")
    
    # Run the specified kernel
    if args.kernel == "naive":
        p99_us = benchmark_vector_add(vector_add_naive, a, b, args.kernel, args.profile_name, False)
    elif args.kernel == "tiled":
        p99_us = benchmark_vector_add(vector_add_tiled, a, b, args.kernel, args.profile_name, False)
    elif args.kernel == "stream":
        p99_us = benchmark_vector_add(vector_add_stream, a, b, args.kernel, args.profile_name, False)
    elif args.kernel == "alloc":
        p99_us = benchmark_vector_add(vector_add_direct_allocation, a, b, args.kernel, args.profile_name, True)
    elif args.kernel == "alloc_bad":
        p99_us = benchmark_vector_add(vector_add_direct_allocation_bad, a, b, args.kernel, args.profile_name, True)
    

if __name__ == "__main__":
    main()
