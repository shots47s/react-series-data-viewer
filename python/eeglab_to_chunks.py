import argparse
import errno
import json
import math
import os
import shutil

import mne.io
import numpy as np
from scipy import signal


try:
    from .protocol_buffers import chunk_pb2 as chunk_pb
except:
    from protocol_buffers import chunk_pb2 as chunk_pb

# Generic code


def pad_channels(channels, chunk_size):
    num_chunks = math.ceil(channels.shape[-1] / chunk_size)
    total_chunked_points = num_chunks * chunk_size
    padding = total_chunked_points - channels.shape[-1]
    padding_seq = [(0, 0) for _ in channels.shape]
    padding_seq[-1] = (0, padding)
    padded_channels = np.pad(
        channels, padding_seq, 'constant', constant_values=0)
    return padded_channels


def channels_to_chunks(channels, chunk_size):
    padded_channels = pad_channels(channels, chunk_size)
    padded_channels = np.expand_dims(padded_channels, axis=-2)
    shape = list(padded_channels.shape)
    shape[-2] = int(shape[-1] / chunk_size)
    shape[-1] = chunk_size
    shape = tuple(shape)
    padded_channels = padded_channels.reshape(shape)
    return padded_channels


def create_chunks_from_channels_list(channels_list, chunk_size):
    channel_chunks_list = [
        channels_to_chunks(channels, chunk_size)
        for channels in channels_list
    ]
    return channel_chunks_list


def downsample_channels(channels, chunk_size, downsampling=0):
    if downsampling == 0:
        return channels
    down = chunk_size**downsampling
    downsampled_size = channels.shape[-1] / down
    if downsampled_size <= chunk_size * 2:
        down = math.floor(channels.shape[-1] / (chunk_size * 2))
    return signal.resample_poly(channels, up=1, down=down, axis=-1)


def create_downsampled_channels_list(channels, chunk_size):
    downsamplings = math.ceil(
        math.log(channels.shape[-1]) / math.log(chunk_size))
    downsamplings = range(0, downsamplings)
    downsampled_channels = [
        downsample_channels(channels, chunk_size, downsampling)
        for downsampling in downsamplings
    ]
    sizes = set()
    unique_sized = []
    for channel in downsampled_channels:
        if channel.shape[-1] in sizes:
            continue
        unique_sized.append(channel)
        sizes.add(channel.shape[-1])
    return unique_sized


def chunk_dir_path(input_path, prefix=None, destination=None):
    base_path, _ = os.path.splitext(input_path)
    root, chunk_dir_name = os.path.split(base_path)
    root = root if destination is None else destination
    prefix = '' if prefix is None else prefix
    chunk_dir = os.path.join(root, prefix, chunk_dir_name)
    return chunk_dir + '.chunks'


def create_path_dirs(path):
    try:
        os.makedirs(path)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise


def write_index_json(chunk_dir, time_interval, chunk_size, downsamplings, channel_chunks_list, trace_types={}):
    json_dict = {
        'timeInterval': time_interval,
        'chunkSize': chunk_size,
        'downsamplings': list(downsamplings),
        'shapes': [
            list(channels.shape)
            for channels in channel_chunks_list
        ],
        'traceTypes': trace_types
    }
    create_path_dirs(chunk_dir)
    with open(os.path.join(chunk_dir, 'index.json'), 'w+') as index_json:
        json.dump(json_dict, index_json)


def encode_chunk(chunk, index, downsampling):
    encoded = chunk_pb.FloatChunk(
        index=index, downsampling=downsampling, samples=chunk)
    return encoded.SerializeToString()


def write_chunks(chunk_dir, channel_chunks_list):
    try:
        shutil.rmtree(os.path.join(chunk_dir, 'raw'))
    except FileNotFoundError as e:
        pass
    except e:
        raise e
    for downsampling, channels in enumerate(channel_chunks_list):
        for channel_index, channel in enumerate(channels):
            for trace_index, trace in enumerate(channel):
                trace_path = os.path.join(
                    chunk_dir,
                    'raw',
                    str(downsampling),
                    str(channel_index),
                    str(trace_index)
                )
                create_path_dirs(trace_path)
                for chunk_index, chunk in enumerate(trace):
                    chunk_path = os.path.join(
                        trace_path, str(chunk_index)) + '.buf'
                    encoded_chunk = encode_chunk(
                        chunk, chunk_index, downsampling)
                    with open(chunk_path, 'w+b') as chunk_file:
                        chunk_file.write(encoded_chunk)


# Specific code

def load_channels(path):
    return mne.io.read_raw_eeglab(path, preload=True)


def eeglab_to_chunks(path, chunk_size):
    parsed = load_channels(path)
    channels = parsed.get_data()
    time_interval = (parsed.times[0], parsed.times[-1])
    channels = np.expand_dims(channels, axis=-2)
    downsampled_channels = create_downsampled_channels_list(
        channels, chunk_size)
    channel_chunks_list = create_chunks_from_channels_list(
        downsampled_channels, chunk_size)
    return channel_chunks_list, time_interval


def write_eeglab_chunks(path, chunk_size, prefix=None, destination=None):
    chunk_dir = chunk_dir_path(path, prefix=prefix, destination=destination)
    channel_chunks_list, time_interval = eeglab_to_chunks(path, chunk_size)
    write_index_json(
        chunk_dir,
        time_interval,
        chunk_size,
        range(len(channel_chunks_list)),
        channel_chunks_list
    )
    write_chunks(chunk_dir, channel_chunks_list)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Convert .set files to chunks for browser based visualisation.')
    parser.add_argument('files', metavar='FILE', type=str, nargs='+',
                        help='one or more .set files to convert to a directory of chunks next to the input file')
    parser.add_argument('--chunk-size', '-s', dest='chunk_size', type=int, default=50000,
                        help='1 dimensional chunk size')
    parser.add_argument('--destination', '-d', dest='destination', type=str,
                        help='optional destination for all the chunk directories')
    parser.add_argument('--prefix', '-p', dest="prefix", type=str,
                        help='optional prefixing parent folder name each directory of chunks gets placed under')

    args = parser.parse_args()
    for path in args.files:
        write_eeglab_chunks(
            path=path,
            chunk_size=args.chunk_size,
            destination=args.destination,
            prefix=args.prefix
        )
