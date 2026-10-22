import os
import stat

from chronicle import probe


def test_ocr_pipes_the_image_and_tolerates_binary_stderr(tmp_path, monkeypatch):
    fake = tmp_path / "tesseract"
    fake.write_text(
        "#!/bin/sh\n"
        '[ "$1" = stdin ] || exit 2\n'
        "cat > /dev/null\n"
        "printf '\\377\\376 not utf-8\\n' >&2\n"
        "printf 'Quarterly  revenue\\n\\nx\\nAcme export\\n'\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"\xff\xd8 not really a jpeg")
    assert probe.ocr(image) == "Quarterly revenue\nAcme export"
    assert probe.ocr(tmp_path / "missing.jpg") is None
