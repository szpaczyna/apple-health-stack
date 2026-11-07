import io
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app

PROLOG = b'<?xml version="1.0"?>\n<!DOCTYPE HealthData [\n<!ELEMENT HealthData ANY>\n]>\n'
FIRST = b' <HealthData locale="pl_PL">\n'
BODY = b"".join(b' <Record type="X" value="%d"/>\n' % i for i in range(2000)) + b"</HealthData>\n"
EXPECTED = FIRST + BODY


def read_all(size: int) -> bytes:
    stream = app._SkipToHealthData(io.BytesIO(PROLOG + EXPECTED))
    if size < 0:
        return stream.read()
    out = bytearray()
    while chunk := stream.read(size):
        out += chunk
    return bytes(out)


class SkipToHealthDataTest(unittest.TestCase):
    def test_drops_the_prolog_and_keeps_every_byte_after_it(self):
        # Sizes around the length of the first line hit the switch from the
        # buffered line to the underlying stream.
        for size in (-1, 1, 7, len(FIRST) - 1, len(FIRST), len(FIRST) + 1, 4096, 10**9):
            with self.subTest(size=size):
                self.assertEqual(read_all(size), EXPECTED)

    def test_random_chunk_sizes(self):
        rnd = random.Random(1)
        for _ in range(50):
            stream = app._SkipToHealthData(io.BytesIO(PROLOG + EXPECTED))
            out = bytearray()
            while chunk := stream.read(rnd.randint(1, 300)):
                out += chunk
            self.assertEqual(bytes(out), EXPECTED)

    def test_stream_without_a_prolog(self):
        stream = app._SkipToHealthData(io.BytesIO(EXPECTED))
        self.assertEqual(stream.read(), EXPECTED)


if __name__ == "__main__":
    unittest.main()
