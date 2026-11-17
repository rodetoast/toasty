"""Tag-boundary splitting for flat RGBA sprites; no Qt or in-place saves.

Public boundaries and result ranges are 1-based, inclusive tag/frame indices.
The binary reader only validates/hashes existing files; Lua performs all edits.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import zlib
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILE_SIZE = 10_000_000
DEFAULT_TIMEOUT = 120


class SplitFailure(Exception):
    def __init__(self, status, message, **details):
        super().__init__(message)
        self.status = status
        self.details = details


@dataclass
class SplitResult:
    status: str
    source: str
    message: str = ""
    outputs: list = field(default_factory=list)
    details: dict = field(default_factory=dict)
    original_unchanged: bool = False

    @property
    def success(self):
        return self.status in ("auto_success", "manual_success", "not_needed")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def needs_split(size, max_size=MAX_FILE_SIZE):
    return size > max_size


def output_name(source, number):
    path = Path(source)
    if type(number) is not int or number < 1:
        raise ValueError("Output number must be a positive integer")
    return f"{path.stem}_{number}{path.suffix}"


def validate_tags(frame_count, tags):
    if not tags:
        raise SplitFailure("no_tags", "The file has no animation tags")
    previous = 0
    for index, tag in enumerate(tags, 1):
        first, last = tag.get("first"), tag.get("last")
        context = dict(tag_index=index, tag_name=tag.get("name"), frame_range=[first, last])
        if type(first) is not int or type(last) is not int or not 1 <= first <= last <= frame_count:
            raise SplitFailure("unsupported_structure", "Invalid tag frame range", **context)
        if first <= previous:
            raise SplitFailure("unsupported_structure", "Tags overlap or are not in frame order", **context)
        if first != previous + 1:
            raise SplitFailure("unsupported_structure", "Frames outside tags are not supported", **context)
        previous = last
    if previous != frame_count:
        raise SplitFailure("unsupported_structure", "Trailing frames outside tags are not supported")


def manual_ranges(tag_count, boundaries):
    try:
        items = list(boundaries)
    except TypeError as exc:
        raise SplitFailure("invalid_boundary", "Boundaries must be integer tag indices") from exc
    if not items or any(type(i) is not int or not 1 <= i < tag_count for i in items):
        raise SplitFailure("invalid_boundary", "Select at least one boundary between tags", boundaries=items)
    cuts = [0, *sorted(set(items)), tag_count]
    return [(a, b) for a, b in zip(cuts, cuts[1:])]


def nearest_boundary(tags, start, stop):
    if stop - start <= 1:
        raise SplitFailure("invalid_boundary", "A single tag cannot be split")
    midpoint = (tags[start]["first"] + tags[stop - 1]["last"]) / 2
    return min(range(start + 1, stop), key=lambda i: (abs(tags[i - 1]["last"] - midpoint), i))


def automatic_ranges(tags, measure, max_size=MAX_FILE_SIZE, initial_size=None):
    """measure(start, stop) saves and returns real bytes; intervals are 0-based tags."""
    if not tags:
        raise SplitFailure("no_tags", "The file has no animation tags")
    pending = [(0, len(tags))]
    finished = []
    visits = 0
    while pending:
        start, stop = pending.pop()
        visits += 1
        if visits > 2 * len(tags) - 1:
            raise SplitFailure("verification_failed", "Split progress bound exceeded")
        size = (initial_size if (start, stop) == (0, len(tags)) and stop - start > 1
                and initial_size is not None else measure(start, stop))
        if type(size) is not int or size <= 0:
            raise SplitFailure("verification_failed", "Candidate has invalid size", tag_range=[start + 1, stop])
        if size <= max_size:
            finished.append((start, stop))
        elif stop - start == 1:
            raise SplitFailure("single_tag_too_large", "A single tag exceeds the size limit",
                               tag_index=start + 1, tag_name=tags[start]["name"],
                               frame_range=[tags[start]["first"], tags[start]["last"]], bytes=size)
        else:
            cut = nearest_boundary(tags, start, stop)
            if not start < cut < stop:
                raise SplitFailure("verification_failed", "Boundary did not reduce the tag count")
            pending.extend([(cut, stop), (start, cut)])
    return sorted(finished)


LUA_SCRIPT = r'''
local s=assert(app.sprite, "Sprite could not be opened")
local function properties(p)
  local result={}
  for k,v in pairs(p) do result[tostring(k)]=tostring(v) end
  assert(next(result)==nil, "User properties are unsupported")
  return result
end
local function color(c) return {c.red,c.green,c.blue,c.alpha} end
local function inspect()
  local d={version=tostring(app.version),api=app.apiVersion,width=s.width,height=s.height,
    colorMode=tostring(s.colorMode),data=s.data,color=color(s.color),properties=properties(s.properties),
    frames={},tags={},layers={},palettes={},cels={},slices=#s.slices,tilesets=#s.tilesets}
  assert(s.colorMode==ColorMode.RGB, "Only RGBA sprites are supported")
  assert(#s.slices==0 and #s.tilesets==0, "Slices and tilesets are unsupported")
  assert(#s.palettes<=1, "Multiple palettes are unsupported")
  for _,f in ipairs(s.frames) do table.insert(d.frames,math.floor(f.duration*1000+0.5)) end
  for _,t in ipairs(s.tags) do
    table.insert(d.tags,{name=t.name,first=t.fromFrame.frameNumber,last=t.toFrame.frameNumber,
      direction=t.aniDir,repeats=t.repeats,color=color(t.color),data=t.data,properties=properties(t.properties)})
  end
  for i,l in ipairs(s.layers) do
    assert(not l.isGroup and not l.isTilemap and not l.isReference, "Only flat image layers are supported")
    table.insert(d.layers,{index=i,name=l.name,visible=l.isVisible,editable=l.isEditable,
      opacity=l.opacity,blend=l.blendMode,data=l.data,color=color(l.color),properties=properties(l.properties)})
  end
  for _,c in ipairs(s.cels) do
    table.insert(d.cels,{frame=c.frame.frameNumber,layer=c.layer.stackIndex,
      data=c.data,color=color(c.color),properties=properties(c.properties)})
  end
  for pi=1,#s.palettes do
    local p=s.palettes[pi]; local colors={}
    for i=0,#p-1 do table.insert(colors,color(p:getColor(i))) end
    table.insert(d.palettes,colors)
  end
  return d
end
if app.params["mode"]=="extract" then
  local first=assert(tonumber(app.params["first"]))
  local last=assert(tonumber(app.params["last"]))
  assert(first>=1 and first<=last and last<=#s.frames, "Invalid extraction range")
  for f=#s.frames,last+1,-1 do s:deleteFrame(f) end
  for f=first-1,1,-1 do s:deleteFrame(f) end
  s:saveCopyAs(app.params["output"])
else
  local d=inspect()
  local f=assert(io.open(app.params["manifest"],"w"))
  f:write(json.encode(d));f:close()
end
'''


def read_palette_chunk(kind, payload):
    """Normalize complete palettes without relying on their on-disk encoding."""
    entries = []
    if kind == 0x0004:
        packets = struct.unpack_from("<H", payload)[0]
        offset = 2
        index = 0
        for _ in range(packets):
            skip, count = struct.unpack_from("<BB", payload, offset)
            offset += 2
            count = count or 256
            index += skip
            if index != len(entries) or index + count > 256:
                raise ValueError("Partial or out-of-range legacy palette")
            for _ in range(count):
                rgb = struct.unpack_from("<BBB", payload, offset)
                offset += 3
                entries.append((*rgb, 255, ""))
            index += count
    else:
        size, first, last = struct.unpack_from("<III", payload)
        if not size or first != 0 or last != size - 1 or any(payload[12:20]):
            raise ValueError("Partial or extended palette")
        offset = 20
        for _ in range(size):
            flags, r, g, b, a = struct.unpack_from("<HBBBB", payload, offset)
            offset += 6
            if flags & ~1:
                raise ValueError("Unknown palette entry flags")
            name = ""
            if flags & 1:
                length = struct.unpack_from("<H", payload, offset)[0]
                offset += 2
                if offset + length > len(payload):
                    raise ValueError("Truncated palette name")
                name = payload[offset:offset + length].decode("utf-8")
                offset += length
            entries.append((r, g, b, a, name))
    if not entries or offset != len(payload):
        raise ValueError("Invalid palette chunk coverage")
    return entries


def read_binary_manifest(path):
    """Read-only strict format subset, independent of Lua's rendered output."""
    data = Path(path).read_bytes()
    static = defaultdict(list)
    frames = []
    u16 = lambda p: struct.unpack_from("<H", data, p)[0]
    u32 = lambda p: struct.unpack_from("<I", data, p)[0]
    try:
        if len(data) < 128 or u16(4) != 0xA5E0 or u32(0) != len(data):
            raise ValueError("Invalid Aseprite header")
        if u16(12) != 32:
            raise SplitFailure("unsupported_structure", "Only 32-bit RGBA files are supported")
        if u32(14) & ~1 or any(data[44:128]):
            raise SplitFailure("unsupported_structure", "Unverified header flags or extensions")
        pos = 128
        tag_chunks = 0
        palettes = defaultdict(list)
        for _ in range(u16(6)):
            if pos + 16 > len(data):
                raise ValueError("Truncated frame")
            size = u32(pos)
            if size < 16 or pos + size > len(data) or u16(pos + 4) != 0xF1FA:
                raise ValueError("Invalid frame")
            count = u32(pos + 12) or u16(pos + 6)
            p = pos + 16
            cels = []
            for _ in range(count):
                if p + 6 > pos + size:
                    raise ValueError("Truncated chunk")
                length, kind = u32(p), u16(p + 4)
                if length < 6 or p + length > pos + size:
                    raise ValueError("Invalid chunk length")
                payload = data[p + 6:p + length]
                if kind == 0x2005:
                    layer, x, y, opacity, cel_type, z = struct.unpack_from("<HhhBHh", payload)
                    if cel_type not in (0, 2):
                        raise SplitFailure("unsupported_structure", "Linked or tilemap Cel is unsupported",
                                           cel_type=cel_type, frame=len(frames) + 1, layer=layer)
                    if any(payload[11:16]):
                        raise SplitFailure("unsupported_structure", "Unverified Cel extensions")
                    w, h = struct.unpack_from("<HH", payload, 16)
                    expected = w * h * 4
                    if cel_type == 2:
                        inflater = zlib.decompressobj()
                        pixels = inflater.decompress(payload[20:], expected + 1)
                        if not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
                            raise ValueError("Invalid compressed Cel")
                    else:
                        pixels = payload[20:]
                    if len(pixels) != expected:
                        raise ValueError("Cel pixel size mismatch")
                    cels.append(dict(layer=layer, x=x, y=y, opacity=opacity, z=z, width=w, height=h,
                                     pixels=hashlib.sha256(pixels).hexdigest()))
                elif kind == 0x2004:
                    flags, layer_type, level = struct.unpack_from("<HHH", payload)
                    if layer_type != 0 or level or flags & ~31:
                        raise SplitFailure("unsupported_structure", "Group, reference or special layer is unsupported")
                    static["layers"].append(hashlib.sha256(payload).hexdigest())
                elif kind in (0x0004, 0x2019):
                    if frames:
                        raise SplitFailure("unsupported_structure", "Animated palettes are unsupported")
                    palettes[kind].append(read_palette_chunk(kind, payload))
                elif kind == 0x2007:
                    static[f"{kind:04x}"].append(hashlib.sha256(payload).hexdigest())
                elif kind == 0x2018:
                    tag_chunks += 1
                elif kind == 0x2020:
                    flags = struct.unpack_from("<I", payload)[0]
                    if flags & ~3:
                        raise SplitFailure("unsupported_structure", "Extended user properties are unsupported")
                else:
                    raise SplitFailure("unsupported_structure", "Unverified Aseprite chunk", chunk=f"{kind:04x}")
                p += length
            if p != pos + size:
                raise ValueError("Frame chunk coverage mismatch")
            frames.append(dict(duration=u16(pos + 8), cels=sorted(cels, key=lambda c: c["layer"])))
            pos += size
        if pos != len(data) or not frames:
            raise ValueError("File/frame coverage mismatch")
        if tag_chunks > 1 or any(len(p) > 1 for p in palettes.values()) or len(static["2007"]) > 1:
            raise SplitFailure("unsupported_structure", "Multiple tag, palette or profile chunks are unsupported")
        # A modern palette overrides its legacy compatibility copy.
        palette = palettes.get(0x2019) or palettes.get(0x0004)
        if palette:
            static["palette"] = palette
        # Legacy speed is regenerated from the first retained frame. Per-frame
        # durations remain authoritative and are compared independently.
        return dict(header=(data[8:18] + data[20:44]).hex(), legacy_speed=u16(18),
                    frames=frames, static=dict(static))
    except (ValueError, struct.error, zlib.error) as exc:
        raise SplitFailure("unsupported_structure", f"Cannot validate file structure: {exc}") from exc


class AsepriteBackend:
    def __init__(self, executable, timeout=DEFAULT_TIMEOUT):
        self.executable = str(executable)
        self.timeout = timeout

    def _run(self, source, work, mode, status, **params):
        script = work / "split.lua"
        script.write_text(LUA_SCRIPT, encoding="utf-8")
        cmd = [self.executable, "-b", str(source)]
        for key, value in dict(mode=mode, **params).items():
            cmd.extend(["--script-param", f"{key}={value}"])
        cmd.extend(["--script", str(script)])
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            def decoded(value):
                return value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")
            raise SplitFailure(status, f"Aseprite invocation failed: {exc}", command=cmd,
                               stdout=decoded(getattr(exc, "stdout", None)),
                               stderr=decoded(getattr(exc, "stderr", None))) from exc
        if result.returncode:
            raise SplitFailure(status, "Aseprite command failed", command=cmd, returncode=result.returncode,
                               stdout=result.stdout.decode("utf-8", "replace"),
                               stderr=result.stderr.decode("utf-8", "replace"))

    def inspect(self, source, work):
        binary = read_binary_manifest(source)
        manifest = work / "inspect.json"
        if manifest.exists():
            manifest.unlink()
        self._run(source, work, "inspect", "reopen_failed", manifest=manifest)
        try:
            info = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SplitFailure("reopen_failed", f"Cannot read Aseprite manifest: {exc}") from exc
        info["binary"] = binary
        return info

    def extract(self, source_copy, output, first, last, work):
        self._run(source_copy, work, "extract", "save_failed", output=output, first=first, last=last)
        if not output.is_file() or output.stat().st_size == 0:
            raise SplitFailure("save_failed", "Aseprite did not create a nonempty output")


def validate_snapshot(info):
    validate_tags(len(info["frames"]), info["tags"])
    if info["api"] < 41 or info["slices"] or info["tilesets"] or len(info["palettes"]) > 1:
        raise SplitFailure("unsupported_structure", "Requires API 41+ and no slices, tilesets or multiple palettes")
    if any(layer.get("group") or layer.get("tilemap") for layer in info["layers"]):
        raise SplitFailure("unsupported_structure", "Only flat image layers are supported")
    if info["frames"] != [f["duration"] for f in info["binary"]["frames"]]:
        raise SplitFailure("verification_failed", "Lua and binary frame durations disagree")


def verify_segment(original, actual, start, stop):
    first = original["tags"][start]["first"]
    last = original["tags"][stop - 1]["last"]
    expected_tags = [dict(tag, first=tag["first"] - first + 1, last=tag["last"] - first + 1)
                     for tag in original["tags"][start:stop]]
    expected_cels = [dict(cel, frame=cel["frame"] - first + 1)
                     for cel in original["cels"] if first <= cel["frame"] <= last]
    comparisons = {
        "tags": actual["tags"] == expected_tags,
        "durations": actual["frames"] == original["frames"][first - 1:last],
        "cel_metadata": actual["cels"] == expected_cels,
        "cel_pixels_positions": actual["binary"]["frames"] == original["binary"]["frames"][first - 1:last],
        "binary_static": actual["binary"]["static"] == original["binary"]["static"],
        "header_metadata": actual["binary"]["header"] == original["binary"]["header"],
    }
    for key in ("width", "height", "colorMode", "data", "color", "properties", "layers", "palettes", "slices", "tilesets"):
        comparisons[key] = actual[key] == original[key]
    failed = [key for key, passed in comparisons.items() if not passed]
    if failed:
        raise SplitFailure("verification_failed", "Output data did not match the original interval", fields=failed)
    return comparisons


def publish_directory(staging, destination):
    if os.path.lexists(destination):
        raise SplitFailure("output_conflict", "Output directory already exists", output=str(destination))
    # Windows rename refuses existing targets. POSIX rename can replace an empty directory.
    if os.name != "nt":
        raise SplitFailure("unsupported_structure", "No-overwrite directory publication currently requires Windows")
    try:
        os.rename(staging, destination)
    except FileExistsError as exc:
        raise SplitFailure("output_conflict", "Output directory appeared during publication") from exc
    except OSError as exc:
        raise SplitFailure("publish_failed", f"Could not publish output directory: {exc}") from exc


@contextmanager
def publication_source_guard(source):
    """Deny writes/deletion during the final hash check and Windows publication."""
    if os.name != "nt":
        raise SplitFailure("unsupported_structure", "Publication currently requires Windows")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(source), 0x80000000, 1, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise SplitFailure("io_failed", "Cannot protect the source during publication",
                           winerror=ctypes.get_last_error())
    try:
        yield
    finally:
        close(handle)


def _cleanup_staging(root, parent):
    if (root.parent != Path(parent).resolve() or not root.name.startswith("atm_split_")
            or root.is_symlink() or getattr(root, "is_junction", lambda: False)()):
        raise OSError("Refusing cleanup outside the owned staging directory")
    marker = root / ".atm_split_staging"
    if not marker.is_file() or marker.is_symlink():
        raise OSError("Refusing cleanup without the owned staging marker")
    # Remove the payload first. A failed cleanup must retain the exclusion marker.
    for child in root.iterdir():
        if child == marker:
            continue
        if child.is_dir() and not child.is_symlink():
            if getattr(child, "is_junction", lambda: False)() or not child.resolve().is_relative_to(root):
                raise OSError("Refusing recursive cleanup outside staging")
            shutil.rmtree(child)
        else:
            try:
                child.unlink()
            except PermissionError:
                if child.is_symlink():
                    raise
                child.chmod(stat.S_IREAD | stat.S_IWRITE)
                child.unlink()
    marker.unlink()
    root.rmdir()


@contextmanager
def staging_workspace(parent):
    root = Path(tempfile.mkdtemp(prefix="atm_split_", dir=parent)).resolve()
    (root / ".atm_split_staging").write_text("Aseprite Tag Master staging", encoding="ascii")
    try:
        yield root
    except BaseException as exc:
        try:
            _cleanup_staging(root, parent)
        except OSError as cleanup:
            context = dict(cleanup_warning=f"Workspace cleanup failed: {cleanup}", staging_path=str(root))
            if isinstance(exc, SplitFailure):
                exc.details.update(context)
            elif isinstance(exc, Exception):
                raise SplitFailure("io_failed", str(exc), **context) from exc
        raise
    else:
        try:
            _cleanup_staging(root, parent)
        except OSError as exc:
            raise SplitFailure("io_failed", f"Workspace cleanup failed: {exc}", staging_path=str(root)) from exc


def inspect_file(source, aseprite_path, *, backend=None, include_small=False):
    """Read an OS-temp snapshot only; never create a final output directory."""
    source = Path(source).resolve()
    before = None
    details = {}
    try:
        if not source.is_file() or source.suffix.lower() not in (".ase", ".aseprite"):
            raise SplitFailure("invalid_input", "Input must be an existing Aseprite file")
        before = sha256(source)
        size = source.stat().st_size
        details.update(bytes=size, sha256_before=before)
        if size == 0:
            raise SplitFailure("invalid_input", "Input file is empty")
        status = "not_needed" if size <= MAX_FILE_SIZE else "ready"
        if status == "ready" or include_small:
            with tempfile.TemporaryDirectory(prefix="atm_inspect_") as temp:
                work = Path(temp)
                snapshot = work / source.name
                shutil.copy2(source, snapshot)
                if sha256(snapshot) != before:
                    raise SplitFailure("source_changed", "Input changed while taking a snapshot")
                try:
                    info = (backend or AsepriteBackend(aseprite_path)).inspect(snapshot, work)
                    if status == "ready":
                        validate_snapshot(info)
                    details.update(tags=info["tags"], frame_count=len(info["frames"]),
                                   tag_count=len(info["tags"]), api=info["api"], version=info["version"])
                except SplitFailure as exc:
                    if status == "ready":
                        raise
                    details["information_error"] = {"status": exc.status, "message": str(exc), **exc.details}
        after = sha256(source)
        details["sha256_after"] = after
        if before != after:
            raise SplitFailure("source_changed", "Input changed during inspection")
        return SplitResult(status, str(source), details=details, original_unchanged=True)
    except SplitFailure as exc:
        status, message = exc.status, str(exc)
        details.update(exc.details)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        status, message = "io_failed", str(exc)
    unchanged = False
    if before is not None:
        try:
            details["sha256_after"] = sha256(source)
            unchanged = before == details["sha256_after"]
            if not unchanged:
                status = "source_changed"
        except OSError:
            pass
    return SplitResult(status, str(source), message, details=details, original_unchanged=unchanged)


def preview_plan(source, tags, frame_count, *, mode="auto", boundaries=None):
    """Unmeasured plan: auto shows only the first provisional midpoint split."""
    validate_tags(frame_count, tags)
    if mode == "manual":
        ranges = manual_ranges(len(tags), boundaries)
    elif mode == "auto":
        cut = nearest_boundary(tags, 0, len(tags)) if len(tags) > 1 else None
        ranges = [(0, cut), (cut, len(tags))] if cut else [(0, len(tags))]
    else:
        raise SplitFailure("invalid_input", "Unknown split mode")
    return dict(provisional=mode == "auto", files=[
        dict(name=output_name(source, i), tag_range=[start + 1, stop],
             frame_range=[tags[start]["first"], tags[stop - 1]["last"]],
             tag_names=[t["name"] for t in tags[start:stop]], bytes=None)
        for i, (start, stop) in enumerate(ranges, 1)])


def is_generated_directory(path):
    """Exclude actual numbered split outputs, backups and marked staging only."""
    path = Path(path)
    name = path.name.lower()
    if name == ".aseprite_backup":
        return True
    if name.startswith("atm_split_") and (path / ".atm_split_staging").is_file():
        return True
    if not name.endswith("_split"):
        return False
    stem = path.name[:-6]
    pattern = re.compile(re.escape(stem) + r"_([1-9][0-9]*)\.(ase|aseprite)$", re.IGNORECASE)
    try:
        files = [p for p in path.iterdir() if p.is_file() and p.suffix.lower() in (".ase", ".aseprite")]
        matches = [pattern.fullmatch(p.name) for p in files]
        return bool(files) and all(matches) and len({m[2].lower() for m in matches}) == 1 and sorted(
            int(m[1]) for m in matches) == list(range(1, len(files) + 1))
    except OSError:
        return False


def collect_input_files(paths):
    files = []
    seen = set()
    def add(path):
        key = os.path.normcase(os.path.realpath(path))
        if key not in seen and not any(is_generated_directory(p) for p in Path(path).parents):
            seen.add(key)
            files.append(str(path))
    for value in paths:
        path = Path(value)
        if any(is_generated_directory(p) for p in (path, *path.parents)):
            continue
        if path.is_file() and path.suffix.lower() in (".ase", ".aseprite"):
            add(path)
        elif path.is_dir():
            for root, directories, names in os.walk(path):
                directories[:] = [d for d in directories if not is_generated_directory(Path(root) / d)]
                for name in sorted(names):
                    candidate = Path(root) / name
                    if candidate.suffix.lower() in (".ase", ".aseprite"):
                        add(candidate)
    return files


def split_file(source, aseprite_path=None, *, mode="auto", boundaries=None,
               output_dir=None, max_size=MAX_FILE_SIZE, backend=None, expected_sha256=None):
    """Return SplitResult; manual oversize ranges are reported without publication.

    Existing outputs are never replaced. No partial final folder is published.
    Failed candidates are removed with staging; results include measured ranges.
    Statuses: not_needed, auto_success, manual_success, single_tag_too_large,
    no_tags, unsupported_structure, invalid_boundary, manual_oversize,
    save_failed, reopen_failed, verification_failed, output_conflict,
    source_changed, invalid_input, io_failed, publish_failed.
    """
    source = Path(source).resolve()
    original_hash = None
    details = {}
    pending_root = None
    try:
        if mode not in ("auto", "manual") or type(max_size) is not int or max_size <= 0:
            raise SplitFailure("invalid_input", "Invalid mode or size limit")
        if not source.is_file() or source.suffix.lower() not in (".ase", ".aseprite"):
            raise SplitFailure("invalid_input", "Input must be an existing .ase or .aseprite file")
        if source.stat().st_size == 0:
            raise SplitFailure("invalid_input", "Input file is empty")
        original_hash = sha256(source)
        details["sha256_before"] = original_hash
        if expected_sha256 is not None and original_hash != expected_sha256:
            raise SplitFailure("source_changed", "Original changed since the preview; inspect it again")
        if mode == "auto" and not needs_split(source.stat().st_size, max_size):
            details["sha256_after"] = sha256(source)
            if details["sha256_after"] != original_hash:
                raise SplitFailure("source_changed", "Original changed during inspection")
            return SplitResult("not_needed", str(source), "File is within the size limit",
                               details=details, original_unchanged=True)
        destination = Path(output_dir).absolute() if output_dir else source.with_name(source.stem + "_split")
        if os.path.lexists(destination):
            raise SplitFailure("output_conflict", "Output directory already exists", output=str(destination))
        if not destination.parent.is_dir():
            raise SplitFailure("invalid_input", "Output parent directory must already exist")
        if backend is None:
            if not aseprite_path:
                raise SplitFailure("invalid_input", "An Aseprite executable path is required")
            backend = AsepriteBackend(aseprite_path)
        with staging_workspace(destination.parent) as work:
            original_copy = work / ("original" + source.suffix)
            shutil.copy2(source, original_copy)
            if sha256(original_copy) != original_hash:
                raise SplitFailure("source_changed", "Input changed while taking the snapshot")
            original = backend.inspect(original_copy, work)
            validate_snapshot(original)
            tags = original["tags"]
            details.update(tag_count=len(tags), frame_count=len(original["frames"]))
            cache = {}

            def materialize(start, stop):
                key = (start, stop)
                if key in cache:
                    return cache[key][0]
                first, last = tags[start]["first"], tags[stop - 1]["last"]
                context = dict(tag_range=[start + 1, stop], frame_range=[first, last],
                               tag_names=[t["name"] for t in tags[start:stop]])
                candidate = work / f"candidate_{start}_{stop}{source.suffix}"
                # Every candidate starts with a fresh byte-identical original snapshot.
                extraction_copy = work / f"extract_input_{start}_{stop}{source.suffix}"
                shutil.copy2(original_copy, extraction_copy)
                try:
                    backend.extract(extraction_copy, candidate, first, last, work)
                    actual = backend.inspect(candidate, work)
                    validate_snapshot(actual)
                    verification = verify_segment(original, actual, start, stop)
                    size = candidate.stat().st_size
                    cache[key] = (size, candidate, verification, sha256(candidate))
                    return size
                except SplitFailure as exc:
                    exc.details = {**context, **exc.details}
                    raise

            if mode == "auto":
                ranges = automatic_ranges(tags, materialize, max_size, source.stat().st_size)
            else:
                ranges = manual_ranges(len(tags), boundaries)
                oversized = []
                for start, stop in ranges:
                    size = materialize(start, stop)
                    if size > max_size:
                        oversized.append(dict(tag_range=[start + 1, stop],
                                              frame_range=[tags[start]["first"], tags[stop - 1]["last"]],
                                              tag_names=[t["name"] for t in tags[start:stop]], bytes=size))
                if oversized:
                    raise SplitFailure("manual_oversize", "Manual boundaries produced oversized intervals",
                                       oversized_ranges=oversized)
            covered_tags = [i for start, stop in ranges for i in range(start, stop)]
            covered_frames = [f for start, stop in ranges
                              for f in range(tags[start]["first"], tags[stop - 1]["last"] + 1)]
            if covered_tags != list(range(len(tags))) or covered_frames != list(range(1, len(original["frames"]) + 1)):
                raise SplitFailure("verification_failed", "Output intervals do not cover each tag/frame exactly once")
            staging = work / "ready"
            staging.mkdir()
            outputs = []
            for number, (start, stop) in enumerate(ranges, 1):
                materialize(start, stop)
                size, candidate, verification, digest = cache[(start, stop)]
                final = staging / output_name(source, number)
                shutil.copy2(candidate, final)
                if final.stat().st_size != size or size > max_size or sha256(final) != digest:
                    raise SplitFailure("verification_failed", "Staged file changed or exceeds the size limit")
                outputs.append(dict(path=str(destination / final.name), bytes=size, sha256=digest,
                                    tag_range=[start + 1, stop],
                                    frame_range=[tags[start]["first"], tags[stop - 1]["last"]],
                                    verification=verification))
            # Keep verified outputs private while the CLI workspace is cleaned.
            pending_root = Path(tempfile.mkdtemp(prefix="atm_split_pending_", dir=destination.parent)).resolve()
            (pending_root / ".atm_split_staging").write_text("Pending publication", encoding="ascii")
            pending_outputs = pending_root / "ready"
            os.rename(staging, pending_outputs)
        with publication_source_guard(source):
            details["sha256_after"] = sha256(source)
            if details["sha256_after"] != original_hash:
                raise SplitFailure("source_changed", "Original changed before output publication")
            publish_directory(pending_outputs, destination)
        return SplitResult(mode + "_success", str(source), "All output intervals verified and published",
                           outputs=outputs, details=details, original_unchanged=True)
    except SplitFailure as exc:
        details.update(exc.details)
        status, message = exc.status, str(exc)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        status, message = "io_failed", f"Split operation failed: {exc}"
    finally:
        if pending_root is not None:
            try:
                _cleanup_staging(pending_root, destination.parent)
            except OSError as exc:
                details["cleanup_warning"] = f"Private staging cleanup failed: {exc}"
                details["staging_path"] = str(pending_root)
    unchanged = False
    if original_hash is not None:
        try:
            details["sha256_after"] = sha256(source)
            unchanged = details["sha256_after"] == original_hash
            if not unchanged:
                status, message = "source_changed", "Original changed during the operation"
        except OSError:
            pass
    return SplitResult(status, str(source), message, details=details, original_unchanged=unchanged)
