import struct

from steam_mfran.imageinfo import image_info


def test_png():
    data = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 640, 480) + b"\x08\x02\x00\x00\x00"
    assert image_info(data) == ("image/png", 640, 480)


def test_gif():
    assert image_info(b"GIF89a" + struct.pack("<HH", 12, 34) + b"\x00" * 8) == ("image/gif", 12, 34)


def test_jpeg():
    sof = b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", 200, 300) + b"\x03" + b"\x00" * 9
    data = b"\xff\xd8" + b"\xff\xe0\x00\x10" + b"JFIF\x00" + b"\x00" * 9 + sof
    assert image_info(data) == ("image/jpeg", 300, 200)


def test_webp_vp8x():
    data = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"VP8X" + b"\x00" * 8 + (99).to_bytes(3, "little") + (49).to_bytes(3, "little")
    assert image_info(data) == ("image/webp", 100, 50)


def test_unknown():
    assert image_info(b"hello world") is None
