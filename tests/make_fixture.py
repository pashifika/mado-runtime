#!/usr/bin/env python3
"""Reproduce identity.onnx with only the Python standard library.

The model is float32[3] input -> Identity -> float32[3] output, using
ONNX IR 8 and ai.onnx opset 13. Field numbers come from the ONNX schema:
https://github.com/onnx/onnx/blob/main/onnx/onnx.proto
No ONNX package or ONNX Runtime wheel is needed to generate the fixture.
"""

import argparse
from pathlib import Path


def varint(value: int) -> bytes:
    """Encode a nonnegative protobuf varint."""
    if value < 0:
        raise ValueError("This fixture only uses nonnegative varints")
    encoded = bytearray()
    while value >= 128:
        encoded.append((value & 127) | 128)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def integer(field: int, value: int) -> bytes:
    return varint(field << 3) + varint(value)


def message(field: int, value: bytes) -> bytes:
    return varint((field << 3) | 2) + varint(len(value)) + value


def string(field: int, value: str) -> bytes:
    return message(field, value.encode("utf-8"))


def value_info(name: str) -> bytes:
    dimension = integer(1, 3)  # TensorShapeProto.Dimension.dim_value
    shape = message(1, dimension)  # TensorShapeProto.dim
    tensor_type = integer(1, 1) + message(2, shape)  # FLOAT, shape
    type_proto = message(1, tensor_type)  # TypeProto.tensor_type
    return string(1, name) + message(2, type_proto)


def make_model() -> bytes:
    node = (
        string(1, "input")
        + string(2, "output")
        + string(3, "identity")
        + string(4, "Identity")
    )
    graph = (
        message(1, node)
        + string(2, "mado-runtime-smoke")
        + message(11, value_info("input"))
        + message(12, value_info("output"))
    )
    opset = string(1, "") + integer(2, 13)
    return (
        integer(1, 8)  # ModelProto.ir_version
        + string(2, "mado-runtime-smoke")  # ModelProto.producer_name
        + message(7, graph)  # ModelProto.graph
        + message(8, opset)  # ModelProto.opset_import
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the checked-in fixture")
    args = parser.parse_args()
    path = Path(__file__).with_name("identity.onnx")
    expected = make_model()
    if args.check:
        if not path.is_file() or path.read_bytes() != expected:
            parser.exit(1, "identity.onnx does not match its deterministic generator\n")
        print(f"Verified {path.name}: {len(expected)} bytes")
    else:
        path.write_bytes(expected)
        print(f"Wrote {path.name}: {len(expected)} bytes")


if __name__ == "__main__":
    main()
