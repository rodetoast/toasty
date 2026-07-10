import sys
import os
import shutil
import json
import re
import subprocess
import tempfile
import traceback
from datetime import datetime
import wordninja
from thefuzz import process, fuzz
from PyQt6.QtWidgets import (QApplication, QMainWindow, QLabel, QVBoxLayout, 
                             QWidget, QPushButton, QFileDialog, QTextEdit, QMessageBox,
                             QListWidget, QHBoxLayout, QLineEdit, QDialog, QTableWidget,
                             QTableWidgetItem, QHeaderView, QGroupBox, QGridLayout,
                             QCheckBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor

# --- Global Dictionary Configuration ---
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
DICT_FILE_PATH = os.path.join(APP_DIR, "custom_dictionary.json")
SETTINGS_FILE_PATH = os.path.join(APP_DIR, "settings.json")
LOG_FILE_PATH = os.path.join(APP_DIR, "aseprite_tag_master_error.log")
DEFAULT_ASEPRITE_PATH = r"C:\Program Files (x86)\Steam\steamapps\common\Aseprite\aseprite.exe"
ASEPRITE_TIMEOUT_SECONDS = 60
COLLISION_ISSUE = "이름 충돌 위험"
DEFAULT_DICTIONARY = [
    # 기존 추출된 핵심 단어들
    "Attack", "Ready", "Groggy", "End", "(Loop)", "Channeling", "Break", "Idle", "Walk", "Run", 
    "Jump", "Fall", "Hit", "Dead", "Skill", "Ultimate", "Phase", "Start", "Intro", "Outro", 
    "Chase", "Dash", "Active", "Combo", "Recovery", "Swap", "Action", "Trait", "Charging", 
    "Finish", "Turn", "Axe", "Swing", "Front", "Back", "Power", "Wave", "Backt", "Heft", 
    "Grind", "Stomp", "First", "Second", "Overdrive", "Colorchange", "Lightning", "Orb", 
    "Thunder", "Riding", "Flip", "Loop", "Scratch", "Aerial", "Down", "Raise", "Move", 
    "Smash", "Fire", "Ball", "Crush", "Slough", "Vomit", "Deactive", "Land", "Airborne", 
    "Rise", "Teleport", "Soul", "Guard", "Bomb", "Rush", "Final", "Full", "Double", "Slash", 
    "Repeat", "Default", "Activate", "Deactivate", "Crystal", "Shoot", "Pounce", "Bedivere", 
    "Expose", "Enhanced", "Last", "Talk", "Tag", "Change", "Pierce", "Vertical", "Javelin", 
    "Dive", "Throw", "Punish", "Thrust", "Counter", "Landing", "Stand", "Cloak", "Off", 
    "Ascend", "Gather", "Casting", "Get", "Weapon", "Human", "Rising", "Call", "Horse", 
    "Chopping", "Tackle", "Uppercut", "Roar", "Mob", "Grab", "Success", "Fail", "Drop", 
    "Explosion", "Destroyed", "Normal", "Mutant", "Shop", "Boss", "Gate", "Mutantl", 
    "Bloodlust", "Calm", "Enter", "Main", "Ultskill", "Exit",
    
    # [새롭게 대량 추가된 게임 개발용 단어 사전]
    # 애니메이션 상태 / 전환 / 기본 행동
    "Transition", "Blend", "Wait", "Cancel", "Interrupt", "Stun", "Knockback", "Knockdown", 
    "Flinch", "Parry", "Dodge", "Evade", "Roll", "Slide", "Hover", "Fly", "Crouch", "Crawl", 
    "Climb", "Swim", "Spawn", "Despawn", "Die", "Revive", "Resurrect", "Sleep", "Wake",
    "Stop", "Go", "Play", "Pause", "Resume", "Skip", "Next", "Prev", "Previous", "Clear",
    "Block", "Avoid", "Escape", "Catch", "Hold", "Drop", "Pull", "Push", "Lift", "Throw",
    
    # 신체 부위 / 방향 / 위치
    "Head", "Body", "Arm", "Leg", "Hand", "Foot", "Left", "Right", "Top", "Bottom", 
    "Upper", "Lower", "Forward", "Backward", "Center", "Core", "Tail", "Wing", "Horn",
    "Eye", "Eye", "Mouth", "Face", "Neck", "Chest", "Back", "Shoulder", "Knee", "Toe",
    "Up", "Down", "Mid", "Middle", "Side", "Edge", "Corner", "Inside", "Outside",
    
    # 무기 / 장비 / 도구
    "Sword", "Shield", "Bow", "Arrow", "Gun", "Rifle", "Spear", "Dagger", "Staff", 
    "Wand", "Hammer", "Whip", "Blade", "Cannon", "Bullet", "Missile", "Projectile",
    "Armor", "Helmet", "Gloves", "Boots", "Ring", "Amulet", "Cape", "Belt", "Bag", "Potion",
    
    # 속성 / 마법 / 이펙트
    "Ice", "Water", "Wind", "Earth", "Nature", "Poison", "Toxic", "Venom", "Light", "Holy",
    "Dark", "Shadow", "Magic", "Spell", "Aura", "Barrier", "Heal", "Buff", "Debuff",
    "Burst", "Spark", "Smoke", "Dust", "Blood", "Gore", "Impact", "Hitbox", "Hurtbox",
    "Fireball", "Frost", "Flame", "Lightning", "Shock", "Burn", "Freeze", "Slow", "Haste",
    
    # UI 및 시스템 / 기타
    "Icon", "Cursor", "Select", "Click", "Press", "Release", "Drag", 
    "Open", "Close", "Show", "Hide", "Fade", "Zoom", "Pan", "Scroll", "Alert", "Warning",
    "Base", "Effect", "FX", "VFX", "SFX", "Particle", "Spine", "Skeletal", "Sprite",
    "Menu", "Button", "Text", "Font", "Label", "Title", "Window", "Panel", "Tab", "Slot",
    "Level", "Stage", "Room", "Area", "Zone", "Map", "World", "Quest", "Task", "Mission"
]
DEFAULT_TYPO_CORRECTIONS = {
    "attck": "Attack",
    "recovry": "Recovery",
    "chanelling": "Channeling",
}

def load_dictionary():
    if os.path.exists(DICT_FILE_PATH):
        try:
            with open(DICT_FILE_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return normalize_words(DEFAULT_DICTIONARY + data)
        except Exception as e:
            write_error_log("Failed to load custom dictionary; using defaults.", e)
    return normalize_words(DEFAULT_DICTIONARY)

def save_dictionary(words_list):
    normalized_words = normalize_words(words_list)
    with open(DICT_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(normalized_words, f, indent=4, ensure_ascii=False)

def normalize_words(words_list):
    normalized = []
    seen = set()
    for word in words_list:
        clean_word = str(word).strip()
        if not clean_word:
            continue
        key = clean_word.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(clean_word)
    return normalized

def load_settings():
    if os.path.exists(SETTINGS_FILE_PATH):
        try:
            with open(SETTINGS_FILE_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            write_error_log("Failed to load settings.", e)
    return {}

def save_settings(settings):
    with open(SETTINGS_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)

def write_error_log(message, exc=None):
    try:
        with open(LOG_FILE_PATH, 'a', encoding='utf-8') as f:
            f.write(f"\n=== {message} ===\n")
            if exc is not None:
                f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except Exception:
        pass

def lua_quote(value):
    return json.dumps(str(value), ensure_ascii=False)

def lua_path_quote(path):
    return lua_quote(str(path).replace("\\", "/"))

def run_aseprite_command(cmd, file_path=None, timeout=ASEPRITE_TIMEOUT_SECONDS):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        lines = [
            "Aseprite command timed out.",
            f"File: {file_path or '(none)'}",
            f"Timeout: {timeout} seconds",
            "Command:",
            " ".join(f'"{part}"' if " " in str(part) else str(part) for part in cmd),
            "stdout:",
            (e.stdout or "").strip() or "(empty)",
            "stderr:",
            (e.stderr or "").strip() or "(empty)",
        ]
        raise Exception("\n".join(lines)) from e

def describe_subprocess_failure(cmd, result, file_path=None):
    lines = [
        "Aseprite command failed.",
        f"File: {file_path or '(none)'}",
        f"Return code: {result.returncode}",
        "Command:",
        " ".join(f'"{part}"' if " " in str(part) else str(part) for part in cmd),
        "stdout:",
        result.stdout.strip() or "(empty)",
        "stderr:",
        result.stderr.strip() or "(empty)",
    ]
    return "\n".join(lines)

def correct_spelling(word, custom_dict, threshold=85, margin=8):
    if len(word) <= 2:
        return word.capitalize(), False
        
    word_lower = word.lower()
    dict_lower = {w.lower(): w for w in custom_dict}
    
    if word_lower in dict_lower:
        return dict_lower[word_lower], False

    typo_target = DEFAULT_TYPO_CORRECTIONS.get(word_lower)
    if typo_target:
        typo_lower = typo_target.lower()
        if typo_lower in dict_lower:
            return dict_lower[typo_lower], True

    choices = list(dict_lower.keys())
    matches = process.extract(word_lower, choices, scorer=fuzz.ratio, limit=2)
    
    if matches and matches[0][1] >= threshold:
        best_match = matches[0]
        corrected_lower = best_match[0]
        second_score = matches[1][1] if len(matches) > 1 else 0

        if word_lower[0] != corrected_lower[0]:
            return word.capitalize(), False
        if corrected_lower.endswith(word_lower) and corrected_lower != word_lower:
            return word.capitalize(), False
        if abs(len(word_lower) - len(corrected_lower)) > 1:
            return word.capitalize(), False
        if best_match[1] - second_score < margin:
            return word.capitalize(), False
        
        corrected_word = dict_lower[corrected_lower]
        return corrected_word, True
        
    return word.capitalize(), False

def format_tag_name(name, custom_dict):
    is_loop = False
    spelling_corrected = False
    
    if "_(Loop)" in name:
        is_loop = True
        name = name.replace("_(Loop)", "")
    elif "(Loop)" in name:
        is_loop = True
        name = name.replace("(Loop)", "")
        
    parts = name.split('_')
    formatted_parts = []
    
    for part in parts:
        if not part:
            continue
        
        # 1. Split by CamelCase/PascalCase First
        sub_words = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\W|$)|\d+', part)
        if not sub_words:
            sub_words = [part]
            
        final_sub_words = []
        for sw in sub_words:
            # 1. 먼저 전체 단어에 대해 스펠링 교정을 시도합니다
            corrected_sw, was_corrected = correct_spelling(sw, custom_dict)
            
            if was_corrected:
                spelling_corrected = True
                final_sub_words.append(corrected_sw)
            elif sw.islower() and len(sw) >= 4:
                # 2. 스펠링 교정이 안 되었고, 전부 소문자라면 wordninja로 쪼개기 시도
                split_sw = wordninja.split(sw)
                
                if len(split_sw) > 1:
                    for w in split_sw:
                        corrected_w, was_corrected_w = correct_spelling(w, custom_dict)
                        if was_corrected_w:
                            spelling_corrected = True
                        final_sub_words.append(corrected_w)
                else:
                    final_sub_words.append(sw[0].upper() + sw[1:])
            else:
                # 3. 그 외의 경우 단순히 첫 글자만 대문자로 처리
                final_sub_words.append(sw[0].upper() + sw[1:])
                
        formatted_parts.append("".join(final_sub_words))
        
    final_name = "_".join(formatted_parts)
    if is_loop:
        final_name += "_(Loop)"
        
    return final_name, is_loop, spelling_corrected

class AnalysisThread(QThread):
    log_signal = pyqtSignal(str)
    result_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(self, files, aseprite_path):
        super().__init__()
        self.files = files
        self.aseprite_path = aseprite_path

    def run(self):
        results = {}
        for file_path in self.files:
            try:
                tags = self.get_tags_from_file(file_path)
                results[file_path] = tags
            except Exception as e:
                write_error_log(f"Error reading {file_path}", e)
                self.error_signal.emit(f"Error reading {os.path.basename(file_path)}: {str(e)}")
        self.result_signal.emit(results)

    def get_tags_from_file(self, file_path):
        json_fd, json_path = tempfile.mkstemp(suffix=".json")
        os.close(json_fd)
        
        lua_script = r"""
local sprite = app.sprite
if not sprite then return end
local file = io.open(app.params["out_json"], "w")
local function json_escape(value)
    value = string.gsub(value, "\\", "\\\\")
    value = string.gsub(value, '"', '\\"')
    value = string.gsub(value, "\n", "\\n")
    value = string.gsub(value, "\r", "\\r")
    value = string.gsub(value, "\t", "\\t")
    return value
end
file:write("[")
for i, tag in ipairs(sprite.tags) do
    local name = json_escape(tag.name)
    file:write('{"name":"' .. name .. '", "repeats":' .. tostring(tag.repeats) .. '}')
    if i < #sprite.tags then file:write(",") end
end
file:write("]")
file:close()
"""
        lua_fd, lua_path = tempfile.mkstemp(suffix=".lua")
        with os.fdopen(lua_fd, 'w', encoding='utf-8') as f:
            f.write(lua_script)

        cmd = [
            self.aseprite_path, "-b", file_path,
            "--script-param", f"out_json={json_path}",
            "--script", lua_path
        ]
        
        try:
            result = run_aseprite_command(cmd, file_path)
            if result.returncode != 0:
                raise Exception(describe_subprocess_failure(cmd, result, file_path))

            with open(json_path, 'r', encoding='utf-8') as f:
                data = f.read()
                tags = json.loads(data) if data else []
            return tags
        finally:
            for temp_path in (json_path, lua_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

class ApplyThread(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()
    error_signal = pyqtSignal(str)

    def __init__(self, file_modifications, aseprite_path):
        super().__init__()
        self.file_modifications = file_modifications 
        self.aseprite_path = aseprite_path

    def run(self):
        for file_path, mods in self.file_modifications.items():
            if not mods:
                continue
            try:
                self.apply_to_file(file_path, mods)
            except Exception as e:
                write_error_log(f"Error modifying {file_path}", e)
                self.error_signal.emit(f"Error modifying {os.path.basename(file_path)}: {str(e)}")
        self.finished_signal.emit()

    def apply_to_file(self, file_path, mods):
        if not os.path.exists(file_path):
            raise Exception(f"원본 파일을 찾을 수 없습니다: {file_path}")
        if not os.path.isfile(file_path):
            raise Exception(f"파일이 아닙니다: {file_path}")
        before_size = os.path.getsize(file_path)
        if before_size <= 0:
            raise Exception(f"원본 파일 크기가 0입니다. 저장을 중단합니다: {file_path}")
        if not os.access(file_path, os.W_OK):
            raise Exception(f"원본 파일에 쓰기 권한이 없습니다: {file_path}")

        # 1. 자동 백업 로직 추가
        try:
            dir_name = os.path.dirname(file_path)
            base_name = os.path.basename(file_path)
            backup_dir = os.path.join(dir_name, ".aseprite_backup")
            
            if not os.path.exists(backup_dir):
                os.makedirs(backup_dir)
                
            # Windows에서 숨김 폴더로 설정 (선택적)
            if os.name == 'nt':
                subprocess.run(['attrib', '+h', backup_dir], capture_output=True)
                
            stem, ext = os.path.splitext(base_name)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(backup_dir, f"{stem}_{timestamp}{ext}")
            counter = 1
            while os.path.exists(backup_path):
                backup_path = os.path.join(backup_dir, f"{stem}_{timestamp}_{counter:02d}{ext}")
                counter += 1

            shutil.copy2(file_path, backup_path)
            if not os.path.exists(backup_path) or os.path.getsize(backup_path) != before_size:
                raise Exception("백업 파일 검증에 실패했습니다.")
            self.log_signal.emit(f"백업 완료: .aseprite_backup/{os.path.basename(backup_path)}")
        except Exception as e:
            raise Exception(f"백업 실패로 저장을 중단합니다: {str(e)}") from e

        # 2. 기존 Lua 스크립트 실행 로직
        lua_script = """
local sprite = app.sprite
if not sprite then return end
local function set_tag(old_name, new_name, set_repeat)
    for i, tag in ipairs(sprite.tags) do
        if tag.name == old_name then
            tag.name = new_name
            if set_repeat then
                tag.repeats = 1
            end
        end
    end
end
"""
        for old_name, new_name, set_repeat in mods:
            set_repeat_str = "true" if set_repeat else "false"
            lua_script += f'\nset_tag({lua_quote(old_name)}, {lua_quote(new_name)}, {set_repeat_str})'
            self.log_signal.emit(f"[{os.path.basename(file_path)}] {old_name} => {new_name}")

        lua_script += f'\nsprite:saveAs({lua_path_quote(file_path)})\n'

        lua_fd, lua_path = tempfile.mkstemp(suffix=".lua")
        with os.fdopen(lua_fd, 'w', encoding='utf-8') as f:
            f.write(lua_script)

        cmd = [self.aseprite_path, "-b", file_path, "--script", lua_path]
        try:
            result = run_aseprite_command(cmd, file_path)
            if result.returncode != 0:
                raise Exception(describe_subprocess_failure(cmd, result, file_path))
            after_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            if after_size <= 0:
                raise Exception("저장 후 파일이 없거나 크기가 0입니다.")
            self.verify_saved_tags(file_path, mods)
        finally:
            try:
                os.remove(lua_path)
            except OSError:
                pass

    def verify_saved_tags(self, file_path, mods):
        tags = AnalysisThread([], self.aseprite_path).get_tags_from_file(file_path)
        for _, new_name, set_repeat in mods:
            matches = [tag for tag in tags if tag.get('name') == new_name]
            if not matches:
                raise Exception(f"저장 후 검증 실패: 태그를 찾을 수 없습니다: {new_name}")
            if set_repeat and not any(tag.get('repeats') == 1 for tag in matches):
                raise Exception(f"저장 후 검증 실패: Repeat=1이 반영되지 않았습니다: {new_name}")
        self.log_signal.emit(f"저장 후 검증 완료: {os.path.basename(file_path)}")

class DictEditorDialog(QDialog):
    def __init__(self, parent, current_dict):
        super().__init__(parent)
        self.setWindowTitle("단어 사전 편집기 (Dictionary Editor)")
        self.resize(400, 500)
        self.current_dict = current_dict
        
        layout = QVBoxLayout(self)
        
        layout.addWidget(QLabel("사전에 등록된 단어들 (스펠링 교정의 기준이 됩니다):"))
        self.text_edit = QTextEdit()
        self.text_edit.setPlainText("\n".join(self.current_dict))
        layout.addWidget(self.text_edit)
        
        btn_layout = QHBoxLayout()
        btn_save = QPushButton("저장 (Save)")
        btn_cancel = QPushButton("취소 (Cancel)")
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)
        
        btn_save.clicked.connect(self.save_and_accept)
        btn_cancel.clicked.connect(self.reject)
        
    def save_and_accept(self):
        text = self.text_edit.toPlainText()
        new_words = [word.strip() for word in text.split('\n') if word.strip()]
        self.current_dict = list(dict.fromkeys(new_words))
        self.accept()

def build_report_entries(analysis_data, custom_dict, replace_pairs=None):
    replace_pairs = replace_pairs or []
    entries = []

    for file_path, tags in analysis_data.items():
        file_entries = []
        final_names = []

        for tag in tags:
            original_name = tag['name']
            intermediate_name = original_name
            for f_str, r_str in replace_pairs:
                intermediate_name = intermediate_name.replace(f_str, r_str)

            new_name, is_loop, spelling_corrected = format_tag_name(intermediate_name, custom_dict)
            set_repeat = is_loop and tag.get('repeats') != 1
            final_name = new_name if (original_name != new_name or set_repeat) else original_name
            final_names.append(final_name)

            issues = []
            if original_name != intermediate_name:
                issues.append("일괄 단어 치환")
            if spelling_corrected:
                issues.append("스펠링 교정")
            elif intermediate_name != new_name:
                issues.append("네이밍 규칙 위반")
            if set_repeat:
                issues.append("Loop 속성(Repeat) 누락")

            file_entries.append({
                'file_path': file_path,
                'old_name': original_name,
                'new_name': new_name,
                'set_repeat': set_repeat,
                'issues': issues,
                'checked': True,
            })

        duplicate_names = {
            name for name in final_names
            if name and final_names.count(name) > 1
        }

        for entry in file_entries:
            if entry['new_name'] in duplicate_names and entry['old_name'] != entry['new_name']:
                entry['issues'].append(COLLISION_ISSUE)
                entry['checked'] = False
            if entry['issues']:
                entries.append(entry)

    return entries

def count_tags(analysis_data):
    return sum(len(tags) for tags in analysis_data.values())

def format_no_issues_message(analysis_data):
    file_count = len(analysis_data)
    tag_count = count_tags(analysis_data)
    return (
        "검수 완료\n\n"
        "문제가 발견되지 않았습니다.\n\n"
        f"분석 파일 수: {file_count}\n"
        f"분석 태그 수: {tag_count}\n"
        "발견된 문제 수: 0\n\n"
        "네이밍 규칙 위반, 스펠링 교정, Loop repeat 누락, 이름 충돌 위험 항목이 없습니다."
    )

def detect_modification_collisions(analysis_data, file_modifications):
    collisions = []

    for file_path, mods in file_modifications.items():
        tags = analysis_data.get(file_path, [])
        selected_by_old_name = {}
        for old_name, new_name, _ in mods:
            selected_by_old_name[old_name] = new_name

        final_name_sources = {}
        changed_old_names = set(selected_by_old_name.keys())

        for tag in tags:
            original_name = tag['name']
            final_name = selected_by_old_name.get(original_name, original_name)
            final_name_sources.setdefault(final_name, []).append(original_name)

        for final_name, old_names in final_name_sources.items():
            unique_old_names = list(dict.fromkeys(old_names))
            if len(old_names) > 1 and any(old_name in changed_old_names for old_name in old_names):
                collisions.append({
                    'file_path': file_path,
                    'new_name': final_name,
                    'old_names': unique_old_names,
                })

    return collisions

def format_collision_message(collisions, max_items=10):
    lines = ["같은 파일 안에 중복 태그명이 생길 수 있어 저장을 시작하지 않았습니다.", ""]
    for collision in collisions[:max_items]:
        lines.append(f"파일: {os.path.basename(collision['file_path'])}")
        lines.append(f"충돌 결과 태그명: {collision['new_name']}")
        lines.append(f"관련 original tag: {', '.join(collision['old_names'])}")
        lines.append("")
    if len(collisions) > max_items:
        lines.append(f"...외 {len(collisions) - max_items}개 충돌")
    lines.append("충돌 항목을 체크 해제하거나 이름 규칙/치환 규칙을 조정한 뒤 다시 시도하세요.")
    return "\n".join(lines)

class ReportDialog(QDialog):
    def __init__(self, parent, analysis_data, custom_dict, replace_pairs=None):
        super().__init__(parent)
        self.setWindowTitle("검수 리포트 (Check Report) - 체크 해제 시 적용 안됨")
        self.resize(1000, 700)
        self.analysis_data = analysis_data
        self.custom_dict = custom_dict
        self.replace_pairs = replace_pairs or []
        self.modifications_to_apply = {} 
        self.checkboxes = []
        self.issue_types_found = set()

        layout = QVBoxLayout(self)
        
        filter_group = QGroupBox("이슈별 일괄 선택/해제 (Batch Selection by Issue)")
        self.filter_layout = QHBoxLayout()
        filter_group.setLayout(self.filter_layout)
        layout.addWidget(filter_group)
        
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["적용(Apply)", "File Name", "Original Tag", "New Tag", "Issue"])
        
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        
        # 행 높이를 넉넉하게 설정하여 텍스트가 잘리지 않게 함
        self.table.verticalHeader().setDefaultSectionSize(35)
        
        layout.addWidget(self.table)

        self.populate_table()
        self.table.resizeRowsToContents() # 내용에 맞춰 다시 한번 행 높이 조절
        self.create_filter_checkboxes()

        btn_layout = QHBoxLayout()
        self.btn_apply = QPushButton("선택 항목 수정 및 저장 (Apply & Save Checked)")
        self.btn_cancel = QPushButton("취소 (Cancel)")
        btn_layout.addWidget(self.btn_apply)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_apply.clicked.connect(self.confirm_and_accept)

    def populate_table(self):
        row_idx = 0
        for entry in build_report_entries(self.analysis_data, self.custom_dict, self.replace_pairs):
            self.table.insertRow(row_idx)

            chk_widget = QWidget()
            chk_layout = QHBoxLayout(chk_widget)
            chk_box = QCheckBox()
            chk_box.setChecked(entry['checked'])
            is_collision = COLLISION_ISSUE in entry['issues']
            if is_collision:
                chk_box.setEnabled(False)
                chk_box.setToolTip("이름 충돌 위험 항목은 안전을 위해 적용할 수 없습니다.")
            chk_layout.addWidget(chk_box)
            chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk_layout.setContentsMargins(0,0,0,0)
            self.table.setCellWidget(row_idx, 0, chk_widget)

            issue_text = ", ".join(entry['issues'])
            for issue in entry['issues']:
                self.issue_types_found.add(issue)

            self.checkboxes.append({
                'checkbox': chk_box,
                'file_path': entry['file_path'],
                'old_name': entry['old_name'],
                'new_name': entry['new_name'],
                'set_repeat': entry['set_repeat'],
                'issues': issue_text,
                'is_collision': is_collision
            })

            self.table.setItem(row_idx, 1, QTableWidgetItem(os.path.basename(entry['file_path'])))
            self.table.setItem(row_idx, 2, QTableWidgetItem(entry['old_name']))

            new_item = QTableWidgetItem(entry['new_name'])
            new_item.setForeground(QColor("#66b2ff"))
            self.table.setItem(row_idx, 3, new_item)

            issue_item = QTableWidgetItem(issue_text)
            issue_item.setForeground(QColor("#ff6b6b"))
            self.table.setItem(row_idx, 4, issue_item)

            row_idx += 1

    def create_filter_checkboxes(self):
        self.chk_all = QCheckBox("전체 선택/해제 (Select All)")
        self.chk_all.setChecked(True)
        self.chk_all.setStyleSheet("font-weight: bold;")
        self.chk_all.stateChanged.connect(self.toggle_all)
        self.filter_layout.addWidget(self.chk_all)
        
        self.issue_filter_boxes = {}
        for issue_type in sorted(list(self.issue_types_found)):
            chk = QCheckBox(issue_type)
            chk.setChecked(True)
            chk.stateChanged.connect(lambda state, itype=issue_type: self.toggle_by_issue(itype, state))
            self.filter_layout.addWidget(chk)
            self.issue_filter_boxes[issue_type] = chk
            
        self.filter_layout.addStretch()

    def toggle_all(self, state):
        is_checked = state == Qt.CheckState.Checked.value
        for chk in self.issue_filter_boxes.values():
            chk.blockSignals(True)
            chk.setChecked(is_checked)
            chk.blockSignals(False)
            
        for item in self.checkboxes:
            if item.get('is_collision'):
                item['checkbox'].setChecked(False)
            else:
                item['checkbox'].setChecked(is_checked)

    def toggle_by_issue(self, issue_type, state):
        is_checked = state == Qt.CheckState.Checked.value
        for item in self.checkboxes:
            if item.get('is_collision'):
                item['checkbox'].setChecked(False)
            elif issue_type in item['issues']:
                item['checkbox'].setChecked(is_checked)

    def confirm_and_accept(self):
        self.modifications_to_apply = {}
        for item in self.checkboxes:
            if item['checkbox'].isChecked():
                path = item['file_path']
                if path not in self.modifications_to_apply:
                    self.modifications_to_apply[path] = []
                self.modifications_to_apply[path].append((item['old_name'], item['new_name'], item['set_repeat']))

        if not self.modifications_to_apply:
            QMessageBox.information(self, "알림", "선택된 항목이 없습니다.")
            self.reject()
            return

        collisions = detect_modification_collisions(self.analysis_data, self.modifications_to_apply)
        if collisions:
            QMessageBox.critical(self, "이름 충돌 위험", format_collision_message(collisions))
            self.modifications_to_apply = {}
            return

        file_count = len(self.modifications_to_apply)
        tag_count = sum(len(mods) for mods in self.modifications_to_apply.values())
        reply = QMessageBox.question(
            self, "저장 확인", 
            (
                "백업을 만든 뒤 선택한 변경사항을 원본 파일에 저장합니다.\n"
                f"대상 파일 수: {file_count}\n"
                f"변경 태그 수: {tag_count}\n"
                "백업 위치: 각 파일 폴더의 .aseprite_backup\n"
                "백업 실패 시 해당 파일 저장은 중단됩니다."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.accept()

class DropListWidget(QListWidget):
    files_dropped = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        files = []
        for url in event.mimeData().urls():
            local_path = url.toLocalFile()
            if os.path.isfile(local_path):
                if local_path.lower().endswith(('.ase', '.aseprite')):
                    files.append(local_path)
            elif os.path.isdir(local_path):
                # 폴더인 경우 하위 폴더까지 모두 검색하여 추가
                for root, _, filenames in os.walk(local_path):
                    for filename in filenames:
                        if filename.lower().endswith(('.ase', '.aseprite')):
                            files.append(os.path.join(root, filename))
                            
        if files:
            self.files_dropped.emit(files)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Aseprite Tag Master")
        self.resize(850, 800)

        # ---------------------------------------------------
        # 전체 UI 모던 스타일링 (QSS)
        # ---------------------------------------------------
        self.setStyleSheet("""
            /* 전체 배경 및 기본 텍스트 */
            QWidget {
                background-color: #2b2b2b;
                color: #e0e0e0;
                font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
                font-size: 13px;
            }

            /* 버튼 스타일 */
            QPushButton {
                background-color: #3d3d3d;
                border: 1px solid #555555;
                border-radius: 6px;
                padding: 6px 12px;
                color: #e0e0e0;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border: 1px solid #666666;
            }
            QPushButton:pressed {
                background-color: #2a2a2a;
            }
            /* 검수 시작 메인 버튼 */
            QPushButton#AnalyzeBtn {
                background-color: #4CAF50;
                color: white;
                font-size: 15px;
                font-weight: bold;
                border: none;
                border-radius: 8px;
            }
            QPushButton#AnalyzeBtn:hover {
                background-color: #45a049;
            }
            QPushButton#AnalyzeBtn:pressed {
                background-color: #3d8b40;
            }

            /* 그룹 박스 (섹션 나누기) */
            QGroupBox {
                border: 1px solid #555555;
                border-radius: 8px;
                margin-top: 25px; /* 타이틀 공간을 위해 마진 증가 */
                padding-top: 15px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 4px 8px; /* 패딩을 늘려 글자가 잘리지 않게 함 */
                color: #4CAF50;
                font-weight: bold;
                font-size: 14px; /* 명시적인 폰트 크기 지정 */
                left: 10px;
                top: 2px;
            }

            /* 리스트 위젯 (드래그 앤 드롭 영역) */
            QListWidget {
                background-color: #1e1e1e;
                border: 2px dashed #555555;
                border-radius: 8px;
                padding: 5px;
                alternate-background-color: #252525;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #333333;
            }
            QListWidget::item:selected {
                background-color: #3a5b3c;
                border-radius: 4px;
            }

            /* 입력 창 */
            QLineEdit, QTextEdit {
                background-color: #1e1e1e;
                border: 1px solid #555555;
                border-radius: 6px;
                padding: 6px;
                color: #ffffff;
            }
            QLineEdit:focus, QTextEdit:focus {
                border: 1px solid #4CAF50;
            }

            /* 테이블 위젯 (단어 치환 칸) */
            QTableWidget {
                background-color: #1e1e1e;
                border: 1px solid #555555;
                border-radius: 6px;
                gridline-color: #333333;
            }
            QHeaderView::section {
                background-color: #333333;
                padding: 6px;
                border: none;
                border-right: 1px solid #444444;
                border-bottom: 1px solid #444444;
                font-weight: bold;
                color: #e0e0e0;
            }
            QTableWidget::item {
                padding: 4px;
            }

            /* 스크롤바 */
            QScrollBar:vertical {
                background-color: #2b2b2b;
                width: 12px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background-color: #555555;
                min-height: 20px;
                border-radius: 6px;
                margin: 2px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #666666;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            
            /* 라벨 */
            QLabel {
                color: #cccccc;
            }
        """)

        self.settings = load_settings()
        self.aseprite_path = self.settings.get("aseprite_path", DEFAULT_ASEPRITE_PATH)
        self.loaded_files = set()
        self.custom_dict = load_dictionary()

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        # 전체 여백 및 간격 넓히기
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # --- 1. Top: Path & Dict Section ---
        group_settings = QGroupBox("환경 설정")
        path_layout = QGridLayout()
        path_layout.setContentsMargins(15, 20, 15, 15)
        path_layout.setSpacing(10)
        
        self.path_label = QLabel(f"Aseprite 경로: {self.aseprite_path}")
        self.path_label.setStyleSheet("color: #aaa; font-size: 12px;")
        btn_path = QPushButton("경로 변경")
        btn_path.setFixedWidth(120)
        btn_path.clicked.connect(self.change_aseprite_path)
        
        btn_dict = QPushButton("📖 스펠링 사전 편집")
        btn_dict.clicked.connect(self.open_dict_editor)
        btn_dict.setMinimumHeight(35)
        
        path_layout.addWidget(self.path_label, 0, 0)
        path_layout.addWidget(btn_path, 0, 1)
        path_layout.addWidget(btn_dict, 1, 0, 1, 2)
        group_settings.setLayout(path_layout)
        layout.addWidget(group_settings)

        # --- 2. Middle: File List Section ---
        group_files = QGroupBox("작업할 Aseprite 파일 목록")
        file_layout = QVBoxLayout()
        file_layout.setContentsMargins(15, 20, 15, 15)
        
        lbl_drop = QLabel("💡 파일을 이곳에 드래그 앤 드롭 하세요 (.ase, .aseprite)")
        lbl_drop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        file_layout.addWidget(lbl_drop)
        
        self.file_list = DropListWidget()
        self.file_list.setAlternatingRowColors(True)
        self.file_list.setMinimumHeight(200) # 리스트 위젯 최소 높이 증가
        self.file_list.files_dropped.connect(self.add_files)
        file_layout.addWidget(self.file_list)
        
        btn_remove = QPushButton("🗑️ 선택한 파일 목록에서 제거")
        btn_remove.clicked.connect(self.remove_selected_files)
        file_layout.addWidget(btn_remove)
        
        group_files.setLayout(file_layout)
        layout.addWidget(group_files, stretch=1)

        # --- 3. Batch Replace Section (Dynamic List) ---
        self.replace_group = QGroupBox("다중 일괄 단어 치환 (선택사항)")
        replace_layout = QVBoxLayout()
        replace_layout.setContentsMargins(15, 20, 15, 15)
        
        btn_replace_ctrl_layout = QHBoxLayout()
        self.btn_add_replace = QPushButton("➕ 단어쌍 추가")
        self.btn_add_replace.setFixedWidth(130)
        self.btn_add_replace.clicked.connect(self.add_replace_row)
        
        self.btn_del_replace = QPushButton("➖ 선택된 항목 삭제")
        self.btn_del_replace.setFixedWidth(150)
        self.btn_del_replace.clicked.connect(self.del_replace_row)
        
        btn_replace_ctrl_layout.addWidget(self.btn_add_replace)
        btn_replace_ctrl_layout.addWidget(self.btn_del_replace)
        btn_replace_ctrl_layout.addStretch()
        replace_layout.addLayout(btn_replace_ctrl_layout)
        
        self.replace_table = QTableWidget(0, 2)
        self.replace_table.setHorizontalHeaderLabels(["찾을 단어 (Find)", "바꿀 단어 (Replace)"])
        self.replace_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.replace_table.verticalHeader().setDefaultSectionSize(40) # 행(Row) 높이를 크게 고정하여 글자가 잘리지 않게 함
        self.replace_table.setMinimumHeight(200) # 테이블 위젯 높이 증가
        self.replace_table.setMaximumHeight(350)
        self.add_replace_row() # 기본 1행 추가
        replace_layout.addWidget(self.replace_table)
        
        self.replace_group.setLayout(replace_layout)
        layout.addWidget(self.replace_group)

        # --- 4. Bottom Action Buttons & Log ---
        action_layout = QVBoxLayout()
        action_layout.setSpacing(10)
        
        btn_analyze = QPushButton("🚀 검수 및 수정 리포트 보기 (Analyze)")
        btn_analyze.setObjectName("AnalyzeBtn") # ID for specific QSS styling
        btn_analyze.setMinimumHeight(55)
        btn_analyze.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_analyze.clicked.connect(self.start_analysis)
        action_layout.addWidget(btn_analyze)
        
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(120)
        self.log_box.setPlaceholderText("작업 로그가 이곳에 표시됩니다...")
        action_layout.addWidget(self.log_box)
        
        layout.addLayout(action_layout)

    def change_aseprite_path(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select aseprite.exe", "", "Executable (*.exe)")
        if path:
            self.aseprite_path = path
            self.path_label.setText(f"Aseprite Path: {self.aseprite_path}")
            self.settings["aseprite_path"] = self.aseprite_path
            save_settings(self.settings)
            self.log_box.append("Aseprite 경로가 저장되었습니다.")

    def open_dict_editor(self):
        dialog = DictEditorDialog(self, self.custom_dict)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.custom_dict = dialog.current_dict
            try:
                save_dictionary(self.custom_dict)
                self.custom_dict = load_dictionary()
                self.log_box.append("단어 사전이 업데이트 되었습니다.")
            except Exception as e:
                write_error_log("Failed to save custom dictionary.", e)
                QMessageBox.critical(self, "사전 저장 실패", f"custom_dictionary.json 저장에 실패했습니다.\n{str(e)}")

    def add_files(self, files):
        for f in files:
            if f not in self.loaded_files:
                self.loaded_files.add(f)
                self.file_list.addItem(f)
        self.log_box.append(f"{len(files)}개 파일이 목록에 추가되었습니다.")

    def remove_selected_files(self):
        for item in self.file_list.selectedItems():
            self.loaded_files.remove(item.text())
            self.file_list.takeItem(self.file_list.row(item))

    def add_replace_row(self):
        row = self.replace_table.rowCount()
        self.replace_table.insertRow(row)
        self.replace_table.setItem(row, 0, QTableWidgetItem(""))
        self.replace_table.setItem(row, 1, QTableWidgetItem(""))

    def del_replace_row(self):
        current_row = self.replace_table.currentRow()
        if current_row >= 0:
            self.replace_table.removeRow(current_row)

    def get_replace_pairs(self):
        pairs = []
        seen_find = set()
        for i in range(self.replace_table.rowCount()):
            find_item = self.replace_table.item(i, 0)
            rep_item = self.replace_table.item(i, 1)
            f_str = find_item.text().strip() if find_item else ""
            r_str = rep_item.text().strip() if rep_item else ""
            if f_str and f_str not in seen_find:
                seen_find.add(f_str)
                pairs.append((f_str, r_str))
        return pairs

    def start_analysis(self):
        if not self.loaded_files:
            QMessageBox.warning(self, "경고", "먼저 파일을 드래그 앤 드롭으로 추가해주세요.")
            return
        if not os.path.exists(self.aseprite_path):
            QMessageBox.critical(self, "에러", "Aseprite 실행 파일을 찾을 수 없습니다.")
            return

        self.log_box.append("파일 분석 중... (Aseprite 백그라운드 실행)")
        self.setEnabled(False)

        self.analysis_thread = AnalysisThread(list(self.loaded_files), self.aseprite_path)
        self.analysis_thread.result_signal.connect(self.show_report)
        self.analysis_thread.error_signal.connect(lambda e: self.log_box.append(f"<font color='red'>{e}</font>"))
        self.analysis_thread.start()

    def show_report(self, analysis_data):
        self.setEnabled(True)
        pairs = self.get_replace_pairs()
        entries = build_report_entries(analysis_data, self.custom_dict, pairs)
        if not entries:
            message = format_no_issues_message(analysis_data)
            self.log_box.append("검수 완료: 문제 없음")
            QMessageBox.information(self, "검수 완료", message)
            return

        dialog = ReportDialog(self, analysis_data, self.custom_dict, pairs)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_modifications(dialog.modifications_to_apply)
        else:
            self.log_box.append("작업이 취소되었습니다.")

    def apply_modifications(self, mods):
        self.log_box.append("파일 수정 및 저장 중...")
        self.setEnabled(False)
        self.apply_thread = ApplyThread(mods, self.aseprite_path)
        self.apply_thread.log_signal.connect(self.log_box.append)
        self.apply_thread.error_signal.connect(lambda e: self.log_box.append(f"<font color='red'>{e}</font>"))
        self.apply_thread.finished_signal.connect(self.on_apply_finished)
        self.apply_thread.start()

    def on_apply_finished(self):
        self.setEnabled(True)
        self.log_box.append("=== 모든 수정 및 저장이 완료되었습니다! ===")
        QMessageBox.information(self, "완료", "파일 저장이 완료되었습니다.")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
