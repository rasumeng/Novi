import pytest

from novi.webui_server import resolve_uploaded_images


def test_uploaded_image_resolution_ignores_client_path_and_mime(tmp_path):
    stored = tmp_path / "att-1.png"
    stored.write_bytes(b"image")

    (image,) = resolve_uploaded_images([{
        "id": "att-1",
        "type": "image",
        "name": "screen.png",
        "mime": "image/jpeg",
        "path": "C:/secrets/not-the-upload.png",
    }], tmp_path)

    assert image.path == str(stored.resolve())
    assert image.media_type == "image/png"


def test_uploaded_image_resolution_rejects_missing_image(tmp_path):
    with pytest.raises(ValueError, match="no longer available"):
        resolve_uploaded_images([{
            "id": "missing", "type": "image", "name": "gone.png",
            "mime": "image/png",
        }], tmp_path)


def test_uploaded_image_resolution_ignores_non_images(tmp_path):
    assert resolve_uploaded_images([{
        "id": "notes", "type": "file", "name": "notes.txt",
        "mime": "text/plain",
    }], tmp_path) == ()
