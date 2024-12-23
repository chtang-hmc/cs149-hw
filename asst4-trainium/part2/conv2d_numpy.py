import numpy as np
import torch


def conv2d_cpu_torch(X, W, bias, pad_size=0, pool_size=2):
    X = torch.tensor(X)
    W = torch.tensor(W)
    bias = torch.tensor(bias)

    conv_out = torch.nn.functional.conv2d(X, W, bias, stride=1, padding=pad_size)

    if pool_size > 1:
        return torch.nn.functional.max_pool2d(
            conv_out, kernel_size=pool_size, stride=pool_size
        )

    return conv_out

"""
A NumPy implementation of the forward pass for a convolutional layer.
"""
def conv_numpy(X, W, bias):
    out = None
    
    batch_size, in_channels, input_height, input_width = X.shape
    out_channels, _, filter_height, filter_width = W.shape

    H_out = 1 + (input_height - filter_height)
    W_out = 1 + (input_width - filter_width)

    out = np.zeros((batch_size, out_channels, H_out, W_out))
    for b in range(batch_size):
        for c in range(out_channels):
            for i in range(H_out):
                for j in range(W_out):
                    x_ij = X[b, :, i : i + filter_height, j : j + filter_width]
                    out[b, c, i, j] = np.sum(x_ij * W[c]) + bias[c]

    return out

"""
A NumPy implementation of the forward pass for a max-pooling layer.
"""
def maxpool_numpy(X, pool_size):
    out = None

    batch_size, in_channels, input_height, input_width = X.shape
    
    H_out = 1 + (input_height - pool_size) // pool_size
    W_out = 1 + (input_width - pool_size) // pool_size

    out = np.zeros((batch_size, in_channels, H_out, W_out))

    for b in range(batch_size):
        for i in range(H_out):
            for j in range(W_out):
                h_start = i * pool_size
                w_start = j * pool_size
                x_ij = X[b, :, h_start : h_start + pool_size, w_start : w_start + pool_size]
                out[b, :, i, j] = np.amax(x_ij, axis=(-1, -2))

    return out
