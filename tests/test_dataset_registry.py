import json
from pathlib import Path

from PIL import Image, ImageDraw

from pod_artwork_engine.contracts import DatasetSplit
from pod_artwork_engine.dataset_registry import DatasetRegistry


def _image(path: Path, *, color=(30, 40, 50), box=(12, 12, 52, 52), fmt=None) -> Path:
    image = Image.new("RGB", (64, 64), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle(box, fill=color)
    image.save(path, format=fmt)
    return path


def test_registry_deduplicates_source_bytes_and_tracks_aliases(tmp_path: Path) -> None:
    registry = DatasetRegistry(tmp_path / "engine.sqlite3", tmp_path / "datasets")
    source_a = _image(tmp_path / "source-a.png")
    source_b = tmp_path / "source-b.png"
    source_b.write_bytes(source_a.read_bytes())
    target = _image(tmp_path / "target.png", color=(160, 20, 20))

    pair = registry.register_pair("design-1", [source_a, source_b], target)

    assert len(pair.source_asset_ids) == 1
    locations = registry.list_asset_locations(pair.source_asset_ids[0])
    assert sorted(locations) == sorted([str(source_a.resolve()), str(source_b.resolve())])


def test_visually_equivalent_targets_share_artwork_identity(tmp_path: Path) -> None:
    registry = DatasetRegistry(tmp_path / "engine.sqlite3", tmp_path / "datasets")
    source = _image(tmp_path / "source.png")
    target_png = _image(tmp_path / "target.png", color=(15, 120, 200))
    target_jpg = _image(
        tmp_path / "target.jpg",
        color=(15, 120, 200),
        fmt="JPEG",
    )

    first = registry.register_pair("design-a", [source], target_png)
    second = registry.register_pair("design-b", [source], target_jpg)

    assert first.artwork_identity == second.artwork_identity


def test_dataset_split_never_leaks_artwork_identity(tmp_path: Path) -> None:
    registry = DatasetRegistry(tmp_path / "engine.sqlite3", tmp_path / "datasets")
    source = _image(tmp_path / "source.png")

    for index in range(10):
        target = _image(
            tmp_path / f"target-{index}.png",
            color=((index * 41) % 255, (index * 73) % 255, (index * 109) % 255),
            box=(4 + index, 6, 28 + index, 54),
        )
        registry.register_pair(f"design-{index}", [source], target)

    dataset = registry.create_dataset("historical", seed="fixed-seed")
    members = registry.list_members(dataset.dataset_id)

    splits_by_artwork: dict[str, set[DatasetSplit]] = {}
    for member in members:
        splits_by_artwork.setdefault(member.artwork_identity, set()).add(member.split)

    assert all(len(splits) == 1 for splits in splits_by_artwork.values())
    assert dataset.artwork_count >= 3
    assert dataset.split_counts[DatasetSplit.GOLDEN_HOLDOUT.value] >= 1
    assert dataset.split_counts[DatasetSplit.VALIDATION.value] >= 1

    golden = registry.list_members(
        dataset.dataset_id,
        split=DatasetSplit.GOLDEN_HOLDOUT,
    )
    retrieval = registry.list_members(dataset.dataset_id, retrieval_only=True)
    assert golden
    assert not {item.pair_id for item in golden} & {item.pair_id for item in retrieval}
    assert all(not item.retrieval_eligible for item in golden)


def test_dataset_manifest_references_original_files_without_copying(tmp_path: Path) -> None:
    registry = DatasetRegistry(tmp_path / "engine.sqlite3", tmp_path / "datasets")
    source = _image(tmp_path / "source.png")
    target = _image(tmp_path / "target.png", color=(200, 60, 40))
    registry.register_pair("design", [source], target)

    dataset = registry.create_dataset("historical")

    manifest = Path(dataset.manifest_path)
    assert manifest.is_file()
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    member = payload["members"][0]
    assert member["sources"][0]["path"] == str(source.resolve())
    assert member["target"]["path"] == str(target.resolve())
    assert not (manifest.parent / source.name).exists()


def test_pair_registration_is_idempotent_across_pair_key_case(tmp_path: Path) -> None:
    registry = DatasetRegistry(tmp_path / "engine.sqlite3", tmp_path / "datasets")
    source = _image(tmp_path / "source.png")
    target = _image(tmp_path / "target.png", color=(90, 20, 180))

    first = registry.register_pair("SKU-ABC", [source], target)
    second = registry.register_pair("sku-abc", [source], target)

    assert second.pair_id == first.pair_id
    assert len(registry.list_pairs()) == 1


def test_split_assignment_is_stable_across_dataset_versions(tmp_path: Path) -> None:
    registry = DatasetRegistry(tmp_path / "engine.sqlite3", tmp_path / "datasets")
    source = _image(tmp_path / "source-stable.png")

    for index in range(6):
        target = _image(
            tmp_path / f"stable-target-{index}.png",
            color=((index * 33 + 20) % 255, (index * 61 + 40) % 255, (index * 97 + 60) % 255),
            box=(8 + index, 8, 38 + index, 52),
        )
        registry.register_pair(f"stable-{index}", [source], target)

    first = registry.create_dataset("historical", seed="seed-a")
    first_members = {
        member.artwork_identity: member.split
        for member in registry.list_members(first.dataset_id)
    }

    for index in range(6, 10):
        target = _image(
            tmp_path / f"stable-target-{index}.png",
            color=((index * 33 + 20) % 255, (index * 61 + 40) % 255, (index * 97 + 60) % 255),
            box=(8 + index, 8, 38 + index, 52),
        )
        registry.register_pair(f"stable-{index}", [source], target)

    second = registry.create_dataset("historical", seed="seed-b")
    second_members = {
        member.artwork_identity: member.split
        for member in registry.list_members(second.dataset_id)
    }

    for identity, split in first_members.items():
        assert second_members[identity] is split
