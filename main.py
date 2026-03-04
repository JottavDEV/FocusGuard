"""FocusGuard - bloqueador de aplicativos em tempo real."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import random
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog
from typing import Callable, Dict, List, Optional, Set, Tuple

import customtkinter as ctk
import psutil
import pystray
from PIL import Image, ImageDraw

try:
    import winreg
except ImportError:  # pragma: no cover - fallback fora do Windows
    winreg = None


@dataclass
class ScheduleRule:
    """Representa uma regra de bloqueio/liberação por período."""

    name: str
    group_name: str
    start_time: str  # HH:MM
    end_time: str  # HH:MM
    mode: str  # "block" ou "allow"
    enabled: bool = True

    def to_dict(self) -> Dict[str, object]:
        """Converte a regra para formato serializável em JSON."""
        return {
            "name": self.name,
            "group_name": self.group_name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "mode": self.mode,
            "enabled": self.enabled,
        }

    @staticmethod
    def from_dict(data: Dict[str, object]) -> ScheduleRule | None:
        """Constrói uma regra validada a partir de dicionário."""
        name = data.get("name")
        group_name = data.get("group_name")
        start_time = data.get("start_time")
        end_time = data.get("end_time")
        mode = data.get("mode")
        enabled = data.get("enabled", True)

        if not isinstance(name, str) or not name.strip():
            return None
        if not isinstance(group_name, str) or not group_name.strip():
            return None
        if not isinstance(start_time, str) or not FocusGuardApp.is_valid_time(start_time):
            return None
        if not isinstance(end_time, str) or not FocusGuardApp.is_valid_time(end_time):
            return None
        if mode not in {"block", "allow"}:
            return None
        if not isinstance(enabled, bool):
            enabled = True

        return ScheduleRule(
            name=name.strip(),
            group_name=group_name.strip(),
            start_time=start_time,
            end_time=end_time,
            mode=mode,
            enabled=enabled,
        )


UNLOCK_LEVELS = ("easy", "medium", "hard", "extreme")
UNLOCK_LEVEL_CHALLENGES = {"easy": 5, "medium": 10, "hard": 25, "extreme": 50}
EXTREME_LIVES = 3


@dataclass
class AppSettings:
    """Configurações persistentes do aplicativo."""

    start_with_windows: bool = False
    unlock_difficulty_enabled: bool = False
    unlock_password_hash: str = ""
    unlock_difficulty_level: str = "medium"

    def to_dict(self) -> Dict[str, object]:
        """Converte settings para dicionário JSON."""
        return {
            "start_with_windows": self.start_with_windows,
            "unlock_difficulty_enabled": self.unlock_difficulty_enabled,
            "unlock_password_hash": self.unlock_password_hash,
            "unlock_difficulty_level": self.unlock_difficulty_level,
        }

    @staticmethod
    def from_dict(data: Dict[str, object]) -> AppSettings:
        """Cria settings a partir de dicionário."""
        startup_value = data.get("start_with_windows", False)
        difficulty_value = data.get("unlock_difficulty_enabled", False)
        password_hash = data.get("unlock_password_hash", "")
        level = data.get("unlock_difficulty_level", "medium")
        if level not in UNLOCK_LEVELS:
            level = "medium"
        return AppSettings(
            start_with_windows=bool(startup_value),
            unlock_difficulty_enabled=bool(difficulty_value),
            unlock_password_hash=password_hash if isinstance(password_hash, str) else "",
            unlock_difficulty_level=level,
        )


def _build_question_pool() -> List[Callable[[], Tuple[str, str]]]:
    """Retorna lista de 50 geradores de perguntas (prompt, resposta) para desbloqueio."""

    def q_add() -> Tuple[str, str]:
        a, b = random.randint(12, 59), random.randint(7, 41)
        return (f"Resolva: {a} + {b} = ?", str(a + b))

    def q_sub() -> Tuple[str, str]:
        a, b = random.randint(30, 99), random.randint(10, 29)
        return (f"Resolva: {a} - {b} = ?", str(a - b))

    def q_mul() -> Tuple[str, str]:
        a, b = random.randint(3, 12), random.randint(4, 12)
        return (f"Resolva: {a} × {b} = ?", str(a * b))

    def q_mul_chain() -> Tuple[str, str]:
        a, b, c = random.randint(2, 9), random.randint(2, 9), random.randint(2, 5)
        return (f"Resolva: ({a} + {b}) × {c} = ?", str((a + b) * c))

    def q_last_digit() -> Tuple[str, str]:
        n = random.randint(100, 999)
        return (f"Qual o último dígito de {n}?", str(n % 10))

    def q_seq_even() -> Tuple[str, str]:
        start = random.randint(2, 10) * 2
        seq = [start + i * 2 for i in range(4)]
        next_val = start + 8
        return (f"Qual o próximo número na sequência: {seq[0]}, {seq[1]}, {seq[2]}, {seq[3]}?", str(next_val))

    def q_seq_odd() -> Tuple[str, str]:
        start = random.randint(1, 9) * 2 - 1
        seq = [start + i * 2 for i in range(4)]
        next_val = start + 8
        return (f"Qual o próximo número na sequência: {seq[0]}, {seq[1]}, {seq[2]}, {seq[3]}?", str(next_val))

    def q_power2() -> Tuple[str, str]:
        exp = random.randint(2, 5)
        base = random.choice([2, 3, 4, 5])
        return (f"Resolva: {base}^{exp} = ?", str(base**exp))

    def q_sqrt() -> Tuple[str, str]:
        n = random.choice([9, 16, 25, 36, 49, 64, 81, 100, 121, 144])
        r = int(n**0.5)
        return (f"Qual a raiz quadrada de {n}?", str(r))

    def q_reverse_word() -> Tuple[str, str]:
        words = ["focus", "guard", "produtividade", "rotina", "controle", "disciplina", "objetivo", "bloqueio", "senha", "tempo"]
        w = random.choice(words)
        return (f"Digite a palavra ao contrário: {w}", w[::-1])

    def q_word_len() -> Tuple[str, str]:
        words = ["palavra", "computador", "teclado", "monitor", "aplicativo", "janela", "arquivo", "pasta"]
        w = random.choice(words)
        return (f"Quantas letras tem a palavra '{w}'?", str(len(w)))

    def q_first_letter() -> Tuple[str, str]:
        words = ["FocusGuard", "Windows", "Bloco", "Senha", "Produto"]
        w = random.choice(words)
        return (f"Qual a primeira letra de '{w}'? (maiúscula ou minúscula)", w[0])

    def q_sum_digits() -> Tuple[str, str]:
        n = random.randint(100, 999)
        ans = sum(int(d) for d in str(n))
        return (f"Some os dígitos de {n}. Qual o resultado?", str(ans))

    def q_pct() -> Tuple[str, str]:
        pct = random.choice([10, 15, 20, 25])
        num = random.choice([200, 300, 400, 100, 500])
        return (f"Quanto é {pct}% de {num}?", str(num * pct // 100))

    def q_minutes() -> Tuple[str, str]:
        h = random.randint(1, 5)
        return (f"Quantos minutos há em {h} hora(s)?", str(h * 60))

    def q_expr1() -> Tuple[str, str]:
        a, b, c = random.randint(2, 6), random.randint(2, 6), random.randint(1, 5)
        return (f"Resolva: {a} × {b} + {c} = ?", str(a * b + c))

    def q_expr2() -> Tuple[str, str]:
        a, b, c = random.randint(5, 12), random.randint(2, 5), random.randint(1, 5)
        return (f"Resolva: ({a} - {b}) × {c} = ?", str((a - b) * c))

    def q_smallest() -> Tuple[str, str]:
        a, b, c = random.sample(range(10, 99), 3)
        return (f"Qual o menor número entre {a}, {b} e {c}?", str(min(a, b, c)))

    def q_median() -> Tuple[str, str]:
        a, b, c = sorted(random.sample(range(10, 99), 3))
        return (f"Qual o número do meio (mediana) entre {a}, {b} e {c}?", str(b))

    def q_sub_big() -> Tuple[str, str]:
        a, b = random.randint(50, 99), random.randint(10, 49)
        return (f"Resolva: {a} - {b} = ?", str(a - b))

    def q_square() -> Tuple[str, str]:
        n = random.randint(5, 15)
        return (f"Quanto é {n} × {n}?", str(n * n))

    def q_mul_small() -> Tuple[str, str]:
        a, b = random.randint(10, 20), random.randint(2, 5)
        return (f"Resolva: {a} × {b} = ?", str(a * b))

    def q_div() -> Tuple[str, str]:
        b = random.randint(2, 12)
        c = random.randint(5, 15)
        a = b * c
        return (f"Resolva: {a} ÷ {b} = ?", str(c))

    def q_sum_four() -> Tuple[str, str]:
        nums = [random.randint(1, 15) for _ in range(4)]
        return (f"Some: {nums[0]} + {nums[1]} + {nums[2]} + {nums[3]} = ?", str(sum(nums)))

    def q_ten_sq() -> Tuple[str, str]:
        return ("Quanto é 10 × 10?", "100")

    def q_add_two() -> Tuple[str, str]:
        a, b = random.randint(15, 49), random.randint(15, 49)
        return (f"Resolva: {a} + {b} = ?", str(a + b))

    def q_sub_two() -> Tuple[str, str]:
        a, b = random.randint(30, 99), random.randint(10, 29)
        return (f"Resolva: {a} - {b} = ?", str(a - b))

    def q_mul67() -> Tuple[str, str]:
        return ("Resolva: 6 × 7 = ?", "42")

    def q_div9() -> Tuple[str, str]:
        n = random.choice([18, 27, 36, 45, 54, 63, 72, 81])
        return (f"Resolva: {n} ÷ 9 = ?", str(n // 9))

    def q_pow2_2() -> Tuple[str, str]:
        e = random.randint(2, 6)
        return (f"Resolva: 2^{e} = ?", str(2**e))

    def q_pow5() -> Tuple[str, str]:
        return ("Quanto é 5²?", "25")

    def q_pow10() -> Tuple[str, str]:
        return ("Quanto é 10²?", "100")

    def q_half_pct() -> Tuple[str, str]:
        return ("Quanto é 50% de 100?", "50")

    def q_quarter() -> Tuple[str, str]:
        n = random.choice([80, 40, 100, 60])
        return (f"Quanto é 1/4 de {n}?", str(n // 4))

    def q_three_quarters() -> Tuple[str, str]:
        n = random.choice([20, 40, 80, 100])
        return (f"Quanto é 3/4 de {n}?", str(3 * n // 4))

    def q_double() -> Tuple[str, str]:
        n = random.randint(20, 49)
        return (f"Quanto é o dobro de {n}?", str(n * 2))

    def q_half() -> Tuple[str, str]:
        n = random.randint(20, 99)
        if n % 2 != 0:
            n -= 1
        return (f"Quanto é a metade de {n}?", str(n // 2))

    def q_add_30_45() -> Tuple[str, str]:
        a, b = random.randint(25, 40), random.randint(40, 55)
        return (f"Resolva: {a} + {b} = ?", str(a + b))

    def q_sub_99() -> Tuple[str, str]:
        a = random.randint(85, 99)
        b = random.randint(10, 25)
        return (f"Resolva: {a} - {b} = ?", str(a - b))

    def q_11_sq() -> Tuple[str, str]:
        return ("Quanto é 11 × 11?", "121")

    def q_13x2() -> Tuple[str, str]:
        return ("Resolva: 13 × 2 = ?", "26")

    def q_100div4() -> Tuple[str, str]:
        return ("Resolva: 100 ÷ 4 = ?", "25")

    def q_72div8() -> Tuple[str, str]:
        return ("Resolva: 72 ÷ 8 = ?", "9")

    def q_add_19_27() -> Tuple[str, str]:
        a, b = random.randint(15, 25), random.randint(22, 35)
        return (f"Resolva: {a} + {b} = ?", str(a + b))

    def q_sub_63() -> Tuple[str, str]:
        a = random.randint(60, 70)
        b = random.randint(25, 35)
        return (f"Resolva: {a} - {b} = ?", str(a - b))

    def q_largest_digit() -> Tuple[str, str]:
        a, b, c = random.randint(0, 9), random.randint(0, 9), random.randint(0, 9)
        return (f"Qual o maior dígito entre {a}, {b} e {c}?", str(max(a, b, c)))

    def q_repeat_number() -> Tuple[str, str]:
        n = random.randint(1000, 9999)
        return (f"Repita exatamente este número: {n}", str(n))

    def q_seq_linear() -> Tuple[str, str]:
        start = random.randint(1, 5)
        step = random.randint(2, 4)
        seq = [start + i * step for i in range(4)]
        next_val = start + 4 * step
        return (f"Qual o próximo número: {seq[0]}, {seq[1]}, {seq[2]}, {seq[3]}?", str(next_val))

    def q_seq_squares() -> Tuple[str, str]:
        seq = [1, 4, 9, 16]
        return ("Qual o próximo número na sequência: 1, 4, 9, 16?", "25")

    def q_144div12() -> Tuple[str, str]:
        return ("Resolva: 144 ÷ 12 = ?", "12")

    return [
        q_add, q_sub, q_mul, q_mul_chain, q_last_digit, q_seq_even, q_seq_odd, q_power2, q_sqrt,
        q_reverse_word, q_word_len, q_first_letter, q_sum_digits, q_pct, q_minutes, q_expr1, q_expr2,
        q_smallest, q_median, q_sub_big, q_square, q_mul_small, q_div, q_sum_four, q_ten_sq,
        q_add_two, q_sub_two, q_mul67, q_div9, q_pow2_2, q_pow5, q_pow10, q_half_pct, q_quarter,
        q_three_quarters, q_double, q_half, q_add_30_45, q_sub_99, q_11_sq, q_13x2, q_100div4,
        q_72div8, q_add_19_27, q_sub_63, q_largest_digit, q_repeat_number, q_seq_linear,
        q_seq_squares, q_144div12,
    ]


UNLOCK_QUESTION_POOL: List[Callable[[], Tuple[str, str]]] = _build_question_pool()


class FocusGuardApp(ctk.CTk):
    """Aplicativo desktop para bloquear processos em tempo real."""

    SCAN_INTERVAL_SECONDS = 1.5
    NOTIFICATION_COOLDOWN_SECONDS = 12.0
    START_MINIMIZED = False
    DEFAULT_GROUP = "Geral"
    RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
    RUN_VALUE_NAME = "FocusGuard"

    def __init__(self) -> None:
        super().__init__()

        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("FocusGuard")
        self.geometry("920x760")
        self.minsize(860, 700)

        self.config_path = self._resolve_config_path()
        self.groups: Dict[str, List[str]] = {self.DEFAULT_GROUP: []}
        self.group_enabled: Dict[str, bool] = {self.DEFAULT_GROUP: True}
        self.rules: List[ScheduleRule] = []
        self.settings = AppSettings()
        self.monitoring_active = False

        self._stop_event = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._data_lock = threading.Lock()
        self._tray_icon: pystray.Icon | None = None
        self._tray_thread: threading.Thread | None = None
        self._is_closing = False
        self._settings_window: ctk.CTkToplevel | None = None
        self._startup_switch_var = ctk.StringVar(value="off")
        self._unlock_switch_var = ctk.StringVar(value="off")
        self._last_notification_at: Dict[str, float] = {}

        self._build_ui()
        self._load_config()
        self._ensure_default_group()
        self._refresh_group_option_menus()
        self._refresh_blocked_list()
        self._refresh_rules_list()
        self._update_status_indicator()
        self._set_admin_warning()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._start_tray_icon()

        if self.START_MINIMIZED:
            self.hide_to_tray()

    def _build_ui(self) -> None:
        """Cria os elementos visuais da interface principal."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        title_label = ctk.CTkLabel(
            self,
            text="FocusGuard",
            font=ctk.CTkFont(size=30, weight="bold"),
        )
        title_label.grid(row=0, column=0, padx=20, pady=(14, 8), sticky="w")

        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=1, column=0, padx=20, pady=(0, 14), sticky="nsew")
        self.tabview.add("Apps e Blocos")
        self.tabview.add("Regras de Horário")
        self.tabview.add("Monitoramento")
        self.tabview.set("Apps e Blocos")

        apps_tab = self.tabview.tab("Apps e Blocos")
        rules_tab = self.tabview.tab("Regras de Horário")
        monitor_tab = self.tabview.tab("Monitoramento")

        apps_tab.grid_columnconfigure(0, weight=1)
        apps_tab.grid_rowconfigure(2, weight=1)
        rules_tab.grid_columnconfigure(0, weight=1)
        rules_tab.grid_rowconfigure(1, weight=1)
        monitor_tab.grid_columnconfigure(0, weight=1)

        process_frame = ctk.CTkFrame(apps_tab)
        process_frame.grid(row=0, column=0, padx=10, pady=(10, 6), sticky="ew")
        process_frame.grid_columnconfigure(0, weight=1)

        self.process_entry = ctk.CTkEntry(
            process_frame,
            placeholder_text="Digite o processo (ex: discord.exe)",
        )
        self.process_entry.grid(row=0, column=0, padx=(10, 8), pady=(10, 8), sticky="ew")
        self.process_entry.bind("<Return>", self._on_add_process_enter)

        add_button = ctk.CTkButton(
            process_frame,
            text="Adicionar",
            width=120,
            command=self.add_process,
        )
        add_button.grid(row=0, column=1, padx=(0, 10), pady=(10, 8))

        self.selected_app_entry = ctk.CTkEntry(
            process_frame,
            placeholder_text="Ou selecione um aplicativo (.exe)",
        )
        self.selected_app_entry.grid(row=1, column=0, padx=(10, 8), pady=8, sticky="ew")
        self.selected_app_entry.configure(state="readonly")

        select_button = ctk.CTkButton(
            process_frame,
            text="Procurar...",
            width=120,
            command=self.select_app_executable,
        )
        select_button.grid(row=1, column=1, padx=(0, 10), pady=8)

        group_target_label = ctk.CTkLabel(process_frame, text="Adicionar no grupo:")
        group_target_label.grid(row=2, column=0, padx=(10, 8), pady=(0, 10), sticky="w")
        self.target_group_menu = ctk.CTkOptionMenu(process_frame, values=[self.DEFAULT_GROUP])
        self.target_group_menu.grid(row=2, column=1, padx=(0, 10), pady=(0, 10), sticky="e")
        self.target_group_menu.set(self.DEFAULT_GROUP)

        group_frame = ctk.CTkFrame(apps_tab)
        group_frame.grid(row=1, column=0, padx=10, pady=6, sticky="ew")
        group_frame.grid_columnconfigure(0, weight=1)

        self.group_entry = ctk.CTkEntry(group_frame, placeholder_text="Nome do bloco (ex: Jogos)")
        self.group_entry.grid(row=0, column=0, padx=(10, 8), pady=10, sticky="ew")

        create_group_button = ctk.CTkButton(
            group_frame,
            text="Criar bloco",
            width=120,
            command=self.create_group,
        )
        create_group_button.grid(row=0, column=1, padx=(0, 8), pady=10)

        self.manage_group_menu = ctk.CTkOptionMenu(group_frame, values=[self.DEFAULT_GROUP], width=160)
        self.manage_group_menu.grid(row=0, column=2, padx=(0, 8), pady=10)
        self.manage_group_menu.set(self.DEFAULT_GROUP)

        rename_group_button = ctk.CTkButton(
            group_frame,
            text="Renomear bloco",
            width=130,
            command=self.rename_group,
        )
        rename_group_button.grid(row=0, column=3, padx=(0, 8), pady=10)

        remove_group_button = ctk.CTkButton(
            group_frame,
            text="Remover bloco",
            width=120,
            fg_color="#9b2c2c",
            hover_color="#7f1d1d",
            command=self.remove_group,
        )
        remove_group_button.grid(row=0, column=4, padx=(0, 10), pady=10)

        list_container = ctk.CTkFrame(apps_tab)
        list_container.grid(row=2, column=0, padx=10, pady=(6, 10), sticky="nsew")
        list_container.grid_columnconfigure(0, weight=1)
        list_container.grid_rowconfigure(0, weight=1)

        self.scrollable_list = ctk.CTkScrollableFrame(list_container, label_text="Aplicativos por bloco")
        self.scrollable_list.grid(row=0, column=0, padx=8, pady=8, sticky="nsew")
        self.scrollable_list.grid_columnconfigure(0, weight=1)

        schedule_form = ctk.CTkFrame(rules_tab)
        schedule_form.grid(row=0, column=0, padx=10, pady=(10, 6), sticky="ew")
        schedule_form.grid_columnconfigure(0, weight=1)

        self.rule_name_entry = ctk.CTkEntry(schedule_form, placeholder_text="Nome da regra (ex: Jogos noite)")
        self.rule_name_entry.grid(row=0, column=0, padx=(10, 8), pady=10, sticky="ew")

        self.rule_mode_menu = ctk.CTkOptionMenu(schedule_form, values=["Bloquear no período", "Liberar no período"])
        self.rule_mode_menu.grid(row=0, column=1, padx=(0, 8), pady=10)
        self.rule_mode_menu.set("Bloquear no período")

        self.rule_group_menu = ctk.CTkOptionMenu(schedule_form, values=[self.DEFAULT_GROUP], width=150)
        self.rule_group_menu.grid(row=0, column=2, padx=(0, 8), pady=10)
        self.rule_group_menu.set(self.DEFAULT_GROUP)

        self.rule_start_entry = ctk.CTkEntry(schedule_form, placeholder_text="Início HH:MM", width=120)
        self.rule_start_entry.grid(row=0, column=3, padx=(0, 8), pady=10)

        self.rule_end_entry = ctk.CTkEntry(schedule_form, placeholder_text="Fim HH:MM", width=120)
        self.rule_end_entry.grid(row=0, column=4, padx=(0, 8), pady=10)

        add_rule_button = ctk.CTkButton(
            schedule_form,
            text="Criar regra",
            width=110,
            command=self.add_rule,
        )
        add_rule_button.grid(row=0, column=5, padx=(0, 10), pady=10)

        self.rules_scrollable = ctk.CTkScrollableFrame(rules_tab, label_text="Regras configuradas")
        self.rules_scrollable.grid(row=1, column=0, padx=10, pady=(4, 10), sticky="nsew")
        self.rules_scrollable.grid_columnconfigure(0, weight=1)

        self.admin_warning_label = ctk.CTkLabel(
            monitor_tab,
            text="",
            text_color="#f5a623",
            font=ctk.CTkFont(size=12),
        )
        self.admin_warning_label.grid(row=0, column=0, padx=12, pady=(12, 6), sticky="w")

        controls_frame = ctk.CTkFrame(monitor_tab)
        controls_frame.grid(row=1, column=0, padx=10, pady=(6, 12), sticky="ew")
        controls_frame.grid_columnconfigure(2, weight=1)

        self.status_led = ctk.CTkLabel(
            controls_frame,
            text="●",
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        self.status_led.grid(row=0, column=0, padx=(12, 6), pady=12, sticky="w")

        self.status_text = ctk.CTkLabel(
            controls_frame,
            text="Status: Inativo",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.status_text.grid(row=0, column=1, padx=(0, 12), pady=12, sticky="w")

        settings_button = ctk.CTkButton(
            controls_frame,
            text="Configurações",
            width=130,
            command=self.open_settings_window,
        )
        settings_button.grid(row=0, column=2, padx=12, pady=12, sticky="e")

        self.toggle_button = ctk.CTkButton(
            controls_frame,
            text="Iniciar Monitoramento",
            width=220,
            command=self.toggle_monitoring,
        )
        self.toggle_button.grid(row=0, column=3, padx=(0, 12), pady=12, sticky="e")

    def _ensure_default_group(self) -> None:
        """Garante a existência do grupo padrão."""
        if self.DEFAULT_GROUP not in self.groups:
            self.groups[self.DEFAULT_GROUP] = []
        if self.DEFAULT_GROUP not in self.group_enabled:
            self.group_enabled[self.DEFAULT_GROUP] = True

    def _set_admin_warning(self) -> None:
        """Exibe aviso caso o app não esteja com privilégios administrativos."""
        if not self._is_running_as_admin():
            self.admin_warning_label.configure(
                text=(
                    "Aviso: executando sem Administrador. "
                    "Alguns processos de sistema podem não ser encerrados."
                )
            )
        else:
            self.admin_warning_label.configure(text="Executando com privilégios de Administrador.")

    @staticmethod
    def _create_tray_image() -> Image.Image:
        """Gera o ícone exibido na bandeja do sistema."""
        size = 64
        image = Image.new("RGB", (size, size), "#121212")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((6, 6, 58, 58), radius=12, fill="#1f1f1f", outline="#2ecc71", width=3)
        draw.ellipse((20, 18, 44, 42), fill="#2ecc71")
        draw.rectangle((30, 38, 34, 50), fill="#2ecc71")
        return image

    def _start_tray_icon(self) -> None:
        """Inicializa o ícone da bandeja em thread separada."""
        if self._tray_icon is not None:
            return

        menu = pystray.Menu(
            pystray.MenuItem("Mostrar FocusGuard", self._on_tray_show, default=True),
            pystray.MenuItem("Iniciar bloqueio", self._on_tray_start_monitoring),
            pystray.MenuItem("Parar bloqueio", self._on_tray_stop_monitoring),
            pystray.MenuItem("Sair", self._on_tray_exit),
        )
        self._tray_icon = pystray.Icon("FocusGuard", self._create_tray_image(), "FocusGuard", menu)
        self._tray_thread = threading.Thread(
            target=self._tray_icon.run,
            name="FocusGuardTrayThread",
            daemon=True,
        )
        self._tray_thread.start()

    def _stop_tray_icon(self) -> None:
        """Interrompe o ícone da bandeja e sua thread associada."""
        if self._tray_icon is not None:
            self._tray_icon.stop()
            self._tray_icon = None

        if self._tray_thread and self._tray_thread.is_alive():
            self._tray_thread.join(timeout=2.5)
        self._tray_thread = None

    def _on_tray_show(self, _: pystray.Icon, __: pystray.MenuItem) -> None:
        """Callback do menu de bandeja para restaurar a janela."""
        self.after(0, self.show_window)

    def _on_tray_start_monitoring(self, _: pystray.Icon, __: pystray.MenuItem) -> None:
        """Callback do menu de bandeja para ativar o monitoramento."""
        self.after(0, self.start_monitoring)

    def _on_tray_stop_monitoring(self, _: pystray.Icon, __: pystray.MenuItem) -> None:
        """Callback do menu de bandeja para desativar o monitoramento."""
        self.after(0, self.stop_monitoring)

    def _on_tray_exit(self, _: pystray.Icon, __: pystray.MenuItem) -> None:
        """Callback do menu de bandeja para encerrar o aplicativo."""
        self.after(0, self.exit_app)

    def hide_to_tray(self) -> None:
        """Oculta a janela principal sem encerrar o bloqueio."""
        self.withdraw()

    def show_window(self) -> None:
        """Restaura a janela principal a partir da bandeja."""
        self.deiconify()
        self.lift()
        self.focus_force()

    @staticmethod
    def _is_running_as_admin() -> bool:
        """Retorna True se o processo atual estiver com privilégios de administrador."""
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    @staticmethod
    def _resolve_config_path() -> Path:
        """Resolve caminho persistente do config.json em AppData do usuário."""
        appdata_path = Path.home() / "AppData" / "Roaming"
        if "APPDATA" in os.environ:
            appdata_path = Path(os.environ["APPDATA"])

        config_dir = appdata_path / "FocusGuard"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "config.json"

    @staticmethod
    def _normalize_process_name(raw_name: str) -> str:
        """Normaliza o nome do processo para minúsculo e com sufixo .exe."""
        process_name = raw_name.strip().lower()
        if not process_name:
            return ""
        if not process_name.endswith(".exe"):
            process_name += ".exe"
        return process_name

    @staticmethod
    def is_valid_time(value: str) -> bool:
        """Valida formato HH:MM."""
        try:
            datetime.strptime(value, "%H:%M")
            return True
        except ValueError:
            return False

    @staticmethod
    def _parse_minutes(value: str) -> int:
        """Converte HH:MM para minutos desde 00:00."""
        parsed = datetime.strptime(value, "%H:%M")
        return parsed.hour * 60 + parsed.minute

    @classmethod
    def _is_time_in_interval(cls, now_minutes: int, start_time: str, end_time: str) -> bool:
        """Retorna True se horário atual está dentro do intervalo."""
        start_minutes = cls._parse_minutes(start_time)
        end_minutes = cls._parse_minutes(end_time)

        if start_minutes == end_minutes:
            return True
        if start_minutes < end_minutes:
            return start_minutes <= now_minutes < end_minutes
        return now_minutes >= start_minutes or now_minutes < end_minutes

    @staticmethod
    def _hash_password(password: str) -> str:
        """Gera hash SHA-256 da senha."""
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    @staticmethod
    def _random_word() -> str:
        """Retorna palavra para puzzle textual."""
        words = [
            "focus",
            "guard",
            "produtividade",
            "rotina",
            "controle",
            "disciplina",
            "objetivo",
        ]
        return random.choice(words)

    def _minigame_memory(self) -> bool:
        """Minigame: memorizar sequência de 8 dígitos exibida por 2,5 s."""
        sequence = "".join(str(random.randint(0, 9)) for _ in range(8))
        tw = ctk.CTkToplevel(self)
        tw.title("Minigame: Memória")
        tw.geometry("320x140")
        tw.resizable(False, False)
        lbl = ctk.CTkLabel(tw, text=sequence, font=ctk.CTkFont(size=28))
        lbl.pack(expand=True, padx=20, pady=20)
        tw.after(2500, tw.destroy)
        tw.wait_window()
        dialog = ctk.CTkInputDialog(
            text="Digite a sequência de 8 dígitos que apareceu:",
            title="Minigame: Memória",
        )
        ans = dialog.get_input()
        return ans is not None and ans.strip() == sequence

    def _minigame_type_exact(self) -> bool:
        """Minigame: memorizar e digitar string de 12 caracteres exibida por 3 s."""
        chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        target = "".join(random.choices(chars, k=12))
        tw = ctk.CTkToplevel(self)
        tw.title("Minigame: Digitação")
        tw.geometry("400x120")
        tw.resizable(False, False)
        lbl = ctk.CTkLabel(tw, text=target, font=ctk.CTkFont(size=22))
        lbl.pack(expand=True, padx=20, pady=20)
        tw.after(3000, tw.destroy)
        tw.wait_window()
        dialog = ctk.CTkInputDialog(
            text="Digite exatamente o que viu (12 caracteres):",
            title="Minigame: Digitação",
        )
        ans = dialog.get_input()
        return ans is not None and ans.strip() == target

    def _minigame_math_chain(self) -> bool:
        """Minigame: expressão aritmética difícil (a+b)×c - d."""
        a, b = random.randint(5, 18), random.randint(5, 18)
        c, d = random.randint(2, 6), random.randint(5, 25)
        result = (a + b) * c - d
        dialog = ctk.CTkInputDialog(
            text=f"Resolva: ({a} + {b}) × {c} - {d} = ?",
            title="Minigame: Cadeia matemática",
        )
        ans = dialog.get_input()
        return ans is not None and ans.strip() == str(result)

    def _minigame_count_letter(self) -> bool:
        """Minigame: contar ocorrências de uma letra em um texto."""
        paragraphs = [
            "O FocusGuard bloqueia aplicativos para aumentar sua produtividade. Use com sabedoria e disciplina.",
            "A rotina de estudos exige foco e persistência. Evite distrações e mantenha o controle do tempo.",
            "Produtividade depende de escolhas diárias. Bloqueie o que atrapalha e libere o que importa.",
        ]
        text = random.choice(paragraphs)
        letter = random.choice("aeosr")
        count = text.lower().count(letter)
        dialog = ctk.CTkInputDialog(
            text=f"Quantas vezes aparece a letra '{letter}' (minúscula) no texto?\n\n{text}",
            title="Minigame: Contagem",
        )
        ans = dialog.get_input()
        return ans is not None and ans.strip() == str(count)

    def _minigame_sequence_next(self) -> bool:
        """Minigame: próximo número em sequência quadrada (1,4,9,16,25 -> 36)."""
        seq = [1, 4, 9, 16, 25]
        dialog = ctk.CTkInputDialog(
            text="Qual o próximo número na sequência: 1, 4, 9, 16, 25?",
            title="Minigame: Sequência",
        )
        ans = dialog.get_input()
        return ans is not None and ans.strip() == "36"

    def _minigame_reaction_sum(self) -> bool:
        """Minigame: soma de três números de dois dígitos."""
        a, b, c = random.randint(10, 49), random.randint(10, 49), random.randint(10, 49)
        result = a + b + c
        dialog = ctk.CTkInputDialog(
            text=f"Some rapidamente: {a} + {b} + {c} = ?",
            title="Minigame: Soma rápida",
        )
        ans = dialog.get_input()
        return ans is not None and ans.strip() == str(result)

    _UNLOCK_MINIGAME_METHODS: List[str] = [
        "_minigame_memory",
        "_minigame_type_exact",
        "_minigame_math_chain",
        "_minigame_count_letter",
        "_minigame_sequence_next",
        "_minigame_reaction_sum",
    ]

    def _build_unlock_steps(self, total: int) -> List[Tuple[str, str, str]]:
        """Monta lista de passos (perguntas e minigames) alternando e embaralhando."""
        n_questions = total // 2
        n_minigames = total - n_questions
        pool = UNLOCK_QUESTION_POOL
        question_steps: List[Tuple[str, str, str]] = []
        for _ in range(n_questions):
            gen = random.choice(pool)
            prompt, answer = gen()
            question_steps.append(("q", prompt, answer))
        methods = self._UNLOCK_MINIGAME_METHODS
        minigame_steps: List[Tuple[str, str, str]] = []
        if methods:
            for i in range(n_minigames):
                minigame_steps.append(("m", methods[i % len(methods)], ""))
        all_steps: List[Tuple[str, str, str]] = question_steps + minigame_steps
        random.shuffle(all_steps)
        return all_steps

    def _request_unlock_challenge(self) -> bool:
        """Executa N desafios (conforme nível) e senha final. EXTREME: 3 vidas, ao errar 3x reseta os 50."""
        with self._data_lock:
            enabled = self.settings.unlock_difficulty_enabled
            password_hash = self.settings.unlock_password_hash
            level = self.settings.unlock_difficulty_level

        if not enabled:
            return True

        if not password_hash:
            return False

        if level not in UNLOCK_LEVELS:
            level = "medium"
        total = UNLOCK_LEVEL_CHALLENGES[level]
        is_extreme = level == "extreme"
        lives = EXTREME_LIVES if is_extreme else 1

        while True:
            all_steps = self._build_unlock_steps(total)
            for index, (kind, prompt_or_name, expected) in enumerate(all_steps, start=1):
                title_suffix = f" ({index}/{total})" + (f" — {lives} vida(s)" if is_extreme else "")
                if kind == "q":
                    dialog = ctk.CTkInputDialog(
                        text=f"{prompt_or_name}\n\nResposta:",
                        title=f"Desbloqueio seguro{title_suffix}",
                    )
                    typed = dialog.get_input()
                    if typed is None:
                        return False
                    if typed.strip().lower() != expected.strip().lower():
                        self._notify_security_error("Resposta incorreta.")
                        if is_extreme:
                            lives -= 1
                            if lives <= 0:
                                self._notify_security_error("3 erros. Recomeçando os 50 desafios.")
                                lives = EXTREME_LIVES
                                break
                            continue
                        return False
                else:
                    method = getattr(self, prompt_or_name, None)
                    if callable(method) and not method():
                        self._notify_security_error("Minigame não concluído corretamente.")
                        if is_extreme:
                            lives -= 1
                            if lives <= 0:
                                self._notify_security_error("3 erros. Recomeçando os 50 desafios.")
                                lives = EXTREME_LIVES
                                break
                            continue
                        return False
            else:
                break

        password_dialog = ctk.CTkInputDialog(
            text="Desafio final\nDigite a senha para liberar o bloqueio:",
            title=f"Desbloqueio seguro ({total + 1}/{total + 1})",
        )
        typed_password = password_dialog.get_input()
        if typed_password is None or not typed_password:
            return False

        if self._hash_password(typed_password) != password_hash:
            self._notify_security_error("Senha incorreta.")
            return False

        return True

    def _notify_security_error(self, message: str) -> None:
        """Notifica falha durante o desbloqueio seguro."""
        if self._tray_icon is not None:
            try:
                self._tray_icon.notify(message, "FocusGuard - desbloqueio negado")
            except Exception:
                return

    def _load_config(self) -> None:
        """Carrega grupos, regras e configurações do config.json."""
        config_source_path = self.config_path
        if not config_source_path.exists():
            legacy_candidates = [
                Path(__file__).with_name("config.json"),
                Path.cwd() / "config.json",
            ]
            for legacy_path in legacy_candidates:
                if legacy_path.exists():
                    config_source_path = legacy_path
                    break

        if not config_source_path.exists():
            self.settings.start_with_windows = self._is_startup_enabled_in_windows()
            return

        try:
            data = json.loads(config_source_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self.settings.start_with_windows = self._is_startup_enabled_in_windows()
            return

        loaded_groups: Dict[str, List[str]] = {}
        groups_data = data.get("groups", {})
        if isinstance(groups_data, dict):
            for group_name, processes in groups_data.items():
                if not isinstance(group_name, str):
                    continue
                if not isinstance(processes, list):
                    continue
                normalized: List[str] = []
                for item in processes:
                    if isinstance(item, str):
                        fixed = self._normalize_process_name(item)
                        if fixed and fixed not in normalized:
                            normalized.append(fixed)
                loaded_groups[group_name.strip()] = normalized

        loaded_group_enabled: Dict[str, bool] = {}
        group_enabled_data = data.get("group_enabled", {})
        if isinstance(group_enabled_data, dict):
            for group_name, enabled_value in group_enabled_data.items():
                if not isinstance(group_name, str):
                    continue
                loaded_group_enabled[group_name.strip()] = bool(enabled_value)

        # Migração da versão antiga (blocked_processes)
        old_blocked = data.get("blocked_processes", [])
        if isinstance(old_blocked, list):
            migrated: List[str] = loaded_groups.get(self.DEFAULT_GROUP, [])
            for process_name in old_blocked:
                if isinstance(process_name, str):
                    fixed = self._normalize_process_name(process_name)
                    if fixed and fixed not in migrated:
                        migrated.append(fixed)
            loaded_groups[self.DEFAULT_GROUP] = migrated

        loaded_rules: List[ScheduleRule] = []
        rules_data = data.get("rules", [])
        if isinstance(rules_data, list):
            for item in rules_data:
                if isinstance(item, dict):
                    rule = ScheduleRule.from_dict(item)
                    if rule is not None:
                        loaded_rules.append(rule)

        loaded_settings = AppSettings.from_dict(data.get("settings", {}) if isinstance(data.get("settings"), dict) else {})
        loaded_settings.start_with_windows = self._is_startup_enabled_in_windows()
        if loaded_settings.unlock_difficulty_enabled and not loaded_settings.unlock_password_hash:
            loaded_settings.unlock_difficulty_enabled = False

        if not loaded_groups:
            loaded_groups = {self.DEFAULT_GROUP: []}

        for group_name in loaded_groups.keys():
            if group_name not in loaded_group_enabled:
                loaded_group_enabled[group_name] = True
        if self.DEFAULT_GROUP not in loaded_group_enabled:
            loaded_group_enabled[self.DEFAULT_GROUP] = True

        with self._data_lock:
            self.groups = loaded_groups
            self.group_enabled = loaded_group_enabled
            self.rules = loaded_rules
            self.settings = loaded_settings

        if config_source_path != self.config_path:
            self._save_config()

    def _save_config(self) -> None:
        """Salva estado completo do app no arquivo config.json."""
        with self._data_lock:
            payload = {
                "groups": self.groups,
                "group_enabled": self.group_enabled,
                "rules": [rule.to_dict() for rule in self.rules],
                "settings": self.settings.to_dict(),
            }

        try:
            self.config_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _all_processes_set(self) -> Set[str]:
        """Retorna conjunto de todos os processos cadastrados em todos os grupos."""
        all_names: Set[str] = set()
        for processes in self.groups.values():
            for process_name in processes:
                all_names.add(process_name)
        return all_names

    def _refresh_group_option_menus(self) -> None:
        """Atualiza valores dos menus de grupo na interface."""
        group_names = sorted(self.groups.keys(), key=lambda item: item.lower())
        if not group_names:
            group_names = [self.DEFAULT_GROUP]

        for group_name in group_names:
            if group_name not in self.group_enabled:
                self.group_enabled[group_name] = True

        self.target_group_menu.configure(values=group_names)
        self.manage_group_menu.configure(values=group_names)
        self.rule_group_menu.configure(values=group_names)

        if self.target_group_menu.get() not in group_names:
            self.target_group_menu.set(group_names[0])
        if self.manage_group_menu.get() not in group_names:
            self.manage_group_menu.set(group_names[0])
        if self.rule_group_menu.get() not in group_names:
            self.rule_group_menu.set(group_names[0])

    def _refresh_blocked_list(self) -> None:
        """Atualiza a lista visual de bloqueios por grupo."""
        for child in self.scrollable_list.winfo_children():
            child.destroy()

        group_names = sorted(self.groups.keys(), key=lambda item: item.lower())
        if not group_names:
            empty_label = ctk.CTkLabel(
                self.scrollable_list,
                text="Nenhum aplicativo configurado.",
                text_color="gray70",
            )
            empty_label.grid(row=0, column=0, padx=10, pady=10, sticky="w")
            return

        for group_row, group_name in enumerate(group_names):
            group_frame = ctk.CTkFrame(self.scrollable_list)
            group_frame.grid(row=group_row, column=0, padx=8, pady=6, sticky="ew")
            group_frame.grid_columnconfigure(0, weight=1)

            header = ctk.CTkFrame(group_frame, fg_color="transparent")
            header.grid(row=0, column=0, padx=6, pady=(6, 4), sticky="ew")
            header.grid_columnconfigure(0, weight=1)

            processes = self.groups.get(group_name, [])
            title = ctk.CTkLabel(
                header,
                text=f"Bloco: {group_name} ({len(processes)} app{'s' if len(processes) != 1 else ''})",
                font=ctk.CTkFont(size=14, weight="bold"),
                anchor="w",
            )
            title.grid(row=0, column=0, padx=(2, 6), pady=2, sticky="ew")

            switch_var = ctk.StringVar(
                value="on" if self.group_enabled.get(group_name, True) else "off"
            )
            enabled_switch = ctk.CTkSwitch(
                header,
                text="Ativo",
                variable=switch_var,
                onvalue="on",
                offvalue="off",
            )
            enabled_switch.configure(
                command=lambda g=group_name, var=switch_var, sw=enabled_switch: self.set_group_enabled(g, var.get() == "on", switch_widget=sw)
            )
            enabled_switch.grid(row=0, column=1, padx=(0, 8), pady=2)
            if self.group_enabled.get(group_name, True):
                enabled_switch.select()
            else:
                enabled_switch.deselect()

            configure_button = ctk.CTkButton(
                header,
                text="⚙",
                width=40,
                command=lambda g=group_name: self.configure_group(g),
            )
            configure_button.grid(row=0, column=2, padx=(0, 2), pady=2, sticky="e")

            if not processes:
                empty_group = ctk.CTkLabel(
                    group_frame,
                    text="Sem aplicativos neste bloco.",
                    text_color="gray70",
                    anchor="w",
                )
                empty_group.grid(row=1, column=0, padx=10, pady=(0, 8), sticky="ew")
                continue

            for process_index, process_name in enumerate(processes):
                row_frame = ctk.CTkFrame(group_frame, fg_color="transparent")
                row_frame.grid(row=process_index + 1, column=0, padx=8, pady=2, sticky="ew")
                row_frame.grid_columnconfigure(0, weight=1)

                label = ctk.CTkLabel(row_frame, text=process_name, anchor="w")
                label.grid(row=0, column=0, padx=(6, 6), pady=4, sticky="ew")

                remove_button = ctk.CTkButton(
                    row_frame,
                    text="Remover",
                    width=90,
                    command=lambda g=group_name, p=process_name: self.remove_process(g, p),
                )
                remove_button.grid(row=0, column=1, padx=(0, 6), pady=4)

    def configure_group(self, group_name: str) -> None:
        """Abre a aba de regras com o bloco selecionado para configuração."""
        self.rule_group_menu.set(group_name)
        self.tabview.set("Regras de Horário")
        self.rule_name_entry.focus_force()

    def set_group_enabled(
        self,
        group_name: str,
        enabled: bool,
        switch_widget: Optional[ctk.CTkSwitch] = None,
    ) -> None:
        """Ativa/desativa bloqueio de um bloco específico. Ao desativar, exige desafio se configurado."""
        with self._data_lock:
            if group_name not in self.groups:
                return
            if not enabled and self.settings.unlock_difficulty_enabled:
                pass
            else:
                self.group_enabled[group_name] = enabled
                self._save_config()
                return

        if not self._request_unlock_challenge():
            if switch_widget is not None:
                switch_widget.select()
            return
        with self._data_lock:
            self.group_enabled[group_name] = False
        self._save_config()

    def _refresh_rules_list(self) -> None:
        """Atualiza lista visual das regras de horário."""
        for child in self.rules_scrollable.winfo_children():
            child.destroy()

        if not self.rules:
            empty_label = ctk.CTkLabel(
                self.rules_scrollable,
                text="Sem regras. Apps permanecem bloqueados conforme o bloco.",
                text_color="gray70",
            )
            empty_label.grid(row=0, column=0, padx=10, pady=10, sticky="w")
            return

        for index, rule in enumerate(self.rules):
            row = ctk.CTkFrame(self.rules_scrollable, fg_color="transparent")
            row.grid(row=index, column=0, padx=8, pady=4, sticky="ew")
            row.grid_columnconfigure(0, weight=1)

            mode_label = "Bloquear no período" if rule.mode == "block" else "Liberar no período"
            rule_label = ctk.CTkLabel(
                row,
                text=f"{rule.name} | {rule.group_name} | {mode_label} | {rule.start_time}-{rule.end_time}",
                anchor="w",
            )
            rule_label.grid(row=0, column=0, padx=(4, 8), pady=4, sticky="ew")

            switch_var = ctk.StringVar(value="on" if rule.enabled else "off")
            switch = ctk.CTkSwitch(
                row,
                text="Ativa",
                variable=switch_var,
                onvalue="on",
                offvalue="off",
                command=lambda idx=index, var=switch_var: self.toggle_rule_enabled(idx, var.get() == "on"),
            )
            switch.grid(row=0, column=1, padx=(0, 8), pady=4)
            if rule.enabled:
                switch.select()
            else:
                switch.deselect()

            remove_button = ctk.CTkButton(
                row,
                text="Excluir",
                width=80,
                fg_color="#9b2c2c",
                hover_color="#7f1d1d",
                command=lambda idx=index: self.remove_rule(idx),
            )
            remove_button.grid(row=0, column=2, padx=(0, 4), pady=4)

    def _update_status_indicator(self) -> None:
        """Atualiza cor e texto do indicador de status."""
        if self.monitoring_active:
            self.status_led.configure(text_color="#2ecc71")
            self.status_text.configure(text="Status: Ativo")
            self.toggle_button.configure(text="Parar Monitoramento")
        else:
            self.status_led.configure(text_color="#e74c3c")
            self.status_text.configure(text="Status: Inativo")
            self.toggle_button.configure(text="Iniciar Monitoramento")

    def _on_add_process_enter(self, _: object) -> None:
        """Permite adicionar processo ao pressionar Enter no campo."""
        self.add_process()

    def _add_process_to_group(self, process_name: str, group_name: str) -> None:
        """Adiciona processo no grupo, movendo caso já exista em outro grupo."""
        for existing_group, processes in self.groups.items():
            if process_name in processes:
                if existing_group == group_name:
                    return
                processes.remove(process_name)
                break

        if group_name not in self.groups:
            self.groups[group_name] = []
        self.groups[group_name].append(process_name)

    def add_process(self) -> None:
        """Adiciona processo digitado ao grupo selecionado."""
        raw_name = self.process_entry.get()
        process_name = self._normalize_process_name(raw_name)
        target_group = self.target_group_menu.get().strip() or self.DEFAULT_GROUP

        if not process_name:
            return

        with self._data_lock:
            self._add_process_to_group(process_name, target_group)

        self.process_entry.delete(0, "end")
        self._save_config()
        self._refresh_blocked_list()

    def select_app_executable(self) -> None:
        """Permite escolher arquivo .exe e adiciona processo ao grupo selecionado."""
        selected_path = filedialog.askopenfilename(
            title="Selecione o aplicativo para bloquear",
            filetypes=[("Executáveis", "*.exe"), ("Todos os arquivos", "*.*")],
        )
        if not selected_path:
            return

        self.selected_app_entry.configure(state="normal")
        self.selected_app_entry.delete(0, "end")
        self.selected_app_entry.insert(0, selected_path)
        self.selected_app_entry.configure(state="readonly")

        process_name = self._normalize_process_name(Path(selected_path).name)
        target_group = self.target_group_menu.get().strip() or self.DEFAULT_GROUP
        if not process_name:
            return

        with self._data_lock:
            self._add_process_to_group(process_name, target_group)

        self._save_config()
        self._refresh_blocked_list()

    def remove_process(self, group_name: str, process_name: str) -> None:
        """Remove processo de um grupo específico."""
        with self._data_lock:
            if group_name not in self.groups:
                return
            if process_name not in self.groups[group_name]:
                return
            self.groups[group_name].remove(process_name)

        self._save_config()
        self._refresh_blocked_list()

    def create_group(self) -> None:
        """Cria novo bloco para organização de aplicativos."""
        name = self.group_entry.get().strip()
        if not name:
            return

        with self._data_lock:
            if name in self.groups:
                self.group_entry.delete(0, "end")
                return
            self.groups[name] = []
            self.group_enabled[name] = True

        self.group_entry.delete(0, "end")
        self._refresh_group_option_menus()
        self._save_config()
        self._refresh_blocked_list()
        self._refresh_rules_list()

    def rename_group(self) -> None:
        """Permite renomear o bloco selecionado."""
        old_name = self.manage_group_menu.get().strip()
        if not old_name:
            return

        if old_name == self.DEFAULT_GROUP:
            self._notify_security_error("O bloco padrão não pode ser renomeado.")
            return

        dialog = ctk.CTkInputDialog(
            text=f"Novo nome para o bloco '{old_name}':",
            title="Renomear bloco",
        )
        new_name_input = dialog.get_input()
        if new_name_input is None:
            return

        new_name = new_name_input.strip()
        if not new_name or new_name == old_name:
            return

        with self._data_lock:
            if new_name in self.groups:
                self._notify_security_error("Já existe um bloco com esse nome.")
                return

            processes = self.groups.pop(old_name, [])
            enabled_state = self.group_enabled.pop(old_name, True)
            self.groups[new_name] = processes
            self.group_enabled[new_name] = enabled_state

            for rule in self.rules:
                if rule.group_name == old_name:
                    rule.group_name = new_name

        self._refresh_group_option_menus()
        self.manage_group_menu.set(new_name)
        self.target_group_menu.set(new_name)
        self.rule_group_menu.set(new_name)
        self._save_config()
        self._refresh_blocked_list()
        self._refresh_rules_list()

    def remove_group(self) -> None:
        """Remove um bloco e move seus processos para o grupo padrão."""
        selected_group = self.manage_group_menu.get().strip()
        if not selected_group or selected_group == self.DEFAULT_GROUP:
            return

        with self._data_lock:
            processes_to_move = list(self.groups.get(selected_group, []))
            self.groups.pop(selected_group, None)
            self.group_enabled.pop(selected_group, None)
            self._ensure_default_group()
            for process_name in processes_to_move:
                if process_name not in self.groups[self.DEFAULT_GROUP]:
                    self.groups[self.DEFAULT_GROUP].append(process_name)

            self.rules = [rule for rule in self.rules if rule.group_name != selected_group]

        self._refresh_group_option_menus()
        self._save_config()
        self._refresh_blocked_list()
        self._refresh_rules_list()

    def add_rule(self) -> None:
        """Cria regra de horário para um bloco."""
        rule_name = self.rule_name_entry.get().strip()
        group_name = self.rule_group_menu.get().strip()
        start_time = self.rule_start_entry.get().strip()
        end_time = self.rule_end_entry.get().strip()
        mode_label = self.rule_mode_menu.get().strip()

        if not rule_name or not group_name:
            return
        if not self.is_valid_time(start_time) or not self.is_valid_time(end_time):
            return

        mode = "block" if mode_label == "Bloquear no período" else "allow"
        new_rule = ScheduleRule(
            name=rule_name,
            group_name=group_name,
            start_time=start_time,
            end_time=end_time,
            mode=mode,
            enabled=True,
        )

        with self._data_lock:
            self.rules.append(new_rule)

        self.rule_name_entry.delete(0, "end")
        self.rule_start_entry.delete(0, "end")
        self.rule_end_entry.delete(0, "end")
        self._save_config()
        self._refresh_rules_list()

    def toggle_rule_enabled(self, index: int, enabled: bool) -> None:
        """Ativa ou desativa uma regra existente."""
        with self._data_lock:
            if index < 0 or index >= len(self.rules):
                return
            self.rules[index].enabled = enabled

        self._save_config()
        self._refresh_rules_list()

    def remove_rule(self, index: int) -> None:
        """Remove regra de horário."""
        with self._data_lock:
            if index < 0 or index >= len(self.rules):
                return
            self.rules.pop(index)

        self._save_config()
        self._refresh_rules_list()

    def _effective_blocked_set(self, now: datetime | None = None) -> Set[str]:
        """Calcula conjunto efetivo de processos que devem ser bloqueados agora."""
        current_time = now or datetime.now()
        now_minutes = current_time.hour * 60 + current_time.minute

        with self._data_lock:
            group_snapshot = {group: list(processes) for group, processes in self.groups.items()}
            enabled_snapshot = {group: self.group_enabled.get(group, True) for group in self.groups.keys()}
            rules_snapshot = list(self.rules)

        all_processes: Set[str] = set()
        for group_name, process_list in group_snapshot.items():
            if not enabled_snapshot.get(group_name, True):
                continue
            all_processes.update(process_list)

        blocked_now: Set[str] = set()
        for process_name in all_processes:
            applicable_rules: List[ScheduleRule] = []
            for rule in rules_snapshot:
                if not rule.enabled:
                    continue
                if not enabled_snapshot.get(rule.group_name, True):
                    continue
                if process_name in group_snapshot.get(rule.group_name, []):
                    applicable_rules.append(rule)

            if not applicable_rules:
                blocked_now.add(process_name)
                continue

            allow_active = False
            block_active = False
            has_allow_rule = False
            has_block_rule = False

            for rule in applicable_rules:
                active = self._is_time_in_interval(now_minutes, rule.start_time, rule.end_time)
                if rule.mode == "allow":
                    has_allow_rule = True
                    if active:
                        allow_active = True
                elif rule.mode == "block":
                    has_block_rule = True
                    if active:
                        block_active = True

            if allow_active:
                continue
            if block_active:
                blocked_now.add(process_name)
                continue
            if has_allow_rule:
                blocked_now.add(process_name)
                continue
            if has_block_rule:
                continue

            blocked_now.add(process_name)

        return blocked_now

    def toggle_monitoring(self) -> None:
        """Inicia ou para a thread de monitoramento de processos."""
        if self.monitoring_active:
            self.stop_monitoring()
        else:
            self.start_monitoring()

    def start_monitoring(self) -> None:
        """Inicia o monitoramento em background."""
        if self.monitoring_active:
            return

        self.monitoring_active = True
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="FocusGuardMonitorThread",
            daemon=True,
        )
        self._monitor_thread.start()
        self._update_status_indicator()

    def stop_monitoring(self, bypass_unlock: bool = False) -> None:
        """Para o monitoramento e aguarda finalização da thread."""
        if not self.monitoring_active:
            return

        if not bypass_unlock and not self._request_unlock_challenge():
            return

        self.monitoring_active = False
        self._stop_event.set()

        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2.5)

        self._monitor_thread = None
        self._update_status_indicator()

    def _monitor_loop(self) -> None:
        """Executa varreduras periódicas para encerrar processos bloqueados."""
        own_pid = psutil.Process().pid

        while not self._stop_event.is_set():
            blocked = self._effective_blocked_set()

            if blocked:
                for proc in psutil.process_iter(["pid", "name"]):
                    if self._stop_event.is_set():
                        break

                    try:
                        info = proc.info
                        pid = info.get("pid")
                        name = info.get("name")

                        if pid == own_pid:
                            continue
                        if not isinstance(name, str):
                            continue
                        if name.lower() not in blocked:
                            continue

                        self._notify_blocked_process(name.lower())
                        proc.terminate()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                    except psutil.Error:
                        continue

            self._stop_event.wait(self.SCAN_INTERVAL_SECONDS)

    def _notify_blocked_process(self, process_name: str) -> None:
        """Notifica quando tentativa de abrir app bloqueado é detectada."""
        now_ts = time.monotonic()
        last_ts = self._last_notification_at.get(process_name, 0.0)
        if now_ts - last_ts < self.NOTIFICATION_COOLDOWN_SECONDS:
            return

        self._last_notification_at[process_name] = now_ts
        if self._tray_icon is None:
            return

        try:
            self._tray_icon.notify(
                f"{process_name} foi bloqueado pelo FocusGuard.",
                "FocusGuard",
            )
        except Exception:
            return

    def _is_startup_enabled_in_windows(self) -> bool:
        """Verifica se o app está registrado para iniciar com o Windows."""
        if winreg is None:
            return False

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, self.RUN_VALUE_NAME)
                return isinstance(value, str) and bool(value.strip())
        except OSError:
            return False

    def _startup_command(self) -> str:
        """Monta comando de inicialização no logon do usuário."""
        if getattr(sys, "frozen", False):
            executable = Path(sys.executable)
            return f'"{executable}"'
        script_path = Path(__file__).resolve()
        return f'"{sys.executable}" "{script_path}"'

    def _set_start_with_windows(self, enabled: bool) -> bool:
        """Ativa/desativa inicialização automática no Windows."""
        if winreg is None:
            return False

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
                if enabled:
                    winreg.SetValueEx(key, self.RUN_VALUE_NAME, 0, winreg.REG_SZ, self._startup_command())
                else:
                    try:
                        winreg.DeleteValue(key, self.RUN_VALUE_NAME)
                    except FileNotFoundError:
                        pass
            return True
        except OSError:
            return False

    def open_settings_window(self) -> None:
        """Abre janela de configurações do aplicativo."""
        if self._settings_window is not None and self._settings_window.winfo_exists():
            self._settings_window.lift()
            self._settings_window.focus_force()
            return

        self._settings_window = ctk.CTkToplevel(self)
        self._settings_window.title("Configurações")
        self._settings_window.geometry("480x340")
        self._settings_window.minsize(420, 300)

        title = ctk.CTkLabel(
            self._settings_window,
            text="Configurações do FocusGuard",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        title.pack(padx=20, pady=(16, 10), anchor="w")

        self._startup_switch_var = ctk.StringVar(
            value="on" if self.settings.start_with_windows else "off"
        )

        startup_switch = ctk.CTkSwitch(
            self._settings_window,
            text="Iniciar automaticamente com o Windows",
            variable=self._startup_switch_var,
            onvalue="on",
            offvalue="off",
            command=self.on_toggle_startup_with_windows,
        )
        startup_switch.pack(padx=20, pady=8, anchor="w")
        if self.settings.start_with_windows:
            startup_switch.select()
        else:
            startup_switch.deselect()

        desc = ctk.CTkLabel(
            self._settings_window,
            text="A configuração é aplicada imediatamente e salva no config.json.",
            text_color="gray70",
        )
        desc.pack(padx=20, pady=(4, 14), anchor="w")

        security_title = ctk.CTkLabel(
            self._settings_window,
            text="Dificuldade de desbloqueio",
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        security_title.pack(padx=20, pady=(0, 8), anchor="w")

        self._unlock_switch_var = ctk.StringVar(
            value="on" if self.settings.unlock_difficulty_enabled else "off"
        )
        unlock_switch = ctk.CTkSwitch(
            self._settings_window,
            text="Exigir puzzles + senha ao tirar bloqueio",
            variable=self._unlock_switch_var,
            onvalue="on",
            offvalue="off",
            command=self.on_toggle_unlock_difficulty,
        )
        unlock_switch.pack(padx=20, pady=(0, 10), anchor="w")
        if self.settings.unlock_difficulty_enabled:
            unlock_switch.select()
        else:
            unlock_switch.deselect()

        set_password_button = ctk.CTkButton(
            self._settings_window,
            text="Definir / alterar senha",
            width=200,
            command=self.set_unlock_password,
        )
        set_password_button.pack(padx=20, pady=(0, 8), anchor="w")

        level_label = ctk.CTkLabel(
            self._settings_window,
            text="Nível de dificuldade dos desafios:",
        )
        level_label.pack(padx=20, pady=(8, 4), anchor="w")

        level_options = [
            "Fácil (5 desafios)",
            "Médio (10 desafios)",
            "Difícil (25 desafios)",
            "EXTREME (50 desafios, 3 vidas)",
        ]
        level_values = ["easy", "medium", "hard", "extreme"]
        current_level = self.settings.unlock_difficulty_level
        try:
            current_index = level_values.index(current_level)
        except ValueError:
            current_index = 1
        self._unlock_level_var = ctk.StringVar(value=level_options[current_index])
        self._unlock_level_menu = ctk.CTkOptionMenu(
            self._settings_window,
            values=level_options,
            variable=self._unlock_level_var,
            command=self._on_unlock_level_changed,
            width=320,
        )
        self._unlock_level_menu.pack(padx=20, pady=(0, 8), anchor="w")

        security_desc = ctk.CTkLabel(
            self._settings_window,
            text=(
                "Quando ativado, será necessário resolver a quantidade de desafios "
                "do nível escolhido (perguntas + minigames) e depois informar a senha. "
                "EXTREME: 3 vidas; ao errar 3 vezes, os 50 desafios recomeçam."
            ),
            text_color="gray70",
            wraplength=430,
            justify="left",
        )
        security_desc.pack(padx=20, pady=(0, 8), anchor="w")

    def on_toggle_startup_with_windows(self) -> None:
        """Callback para ligar/desligar inicialização com Windows."""
        enabled = self._startup_switch_var.get() == "on"
        applied = self._set_start_with_windows(enabled)
        if not applied:
            enabled = self._is_startup_enabled_in_windows()

        with self._data_lock:
            self.settings.start_with_windows = enabled

        self._save_config()

    def set_unlock_password(self) -> None:
        """Define ou atualiza senha para desbloqueio seguro."""
        first_dialog = ctk.CTkInputDialog(
            text="Digite a nova senha de desbloqueio:",
            title="Senha de desbloqueio",
        )
        first_value = first_dialog.get_input()
        if first_value is None or not first_value.strip():
            return

        second_dialog = ctk.CTkInputDialog(
            text="Confirme a nova senha:",
            title="Confirmar senha",
        )
        second_value = second_dialog.get_input()
        if second_value is None:
            return

        if first_value != second_value:
            self._notify_security_error("As senhas não conferem.")
            return

        with self._data_lock:
            self.settings.unlock_password_hash = self._hash_password(first_value)

        self._save_config()
        if self._tray_icon is not None:
            try:
                self._tray_icon.notify("Senha de desbloqueio atualizada com sucesso.", "FocusGuard")
            except Exception:
                return

    def _on_unlock_level_changed(self, choice: str) -> None:
        """Salva o nível de dificuldade escolhido (Fácil/Médio/Difícil/EXTREME)."""
        mapping = {
            "Fácil (5 desafios)": "easy",
            "Médio (10 desafios)": "medium",
            "Difícil (25 desafios)": "hard",
            "EXTREME (50 desafios, 3 vidas)": "extreme",
        }
        level = mapping.get(choice, "medium")
        with self._data_lock:
            self.settings.unlock_difficulty_level = level
        self._save_config()

    def on_toggle_unlock_difficulty(self) -> None:
        """Ativa/desativa exigência de puzzles e senha ao desbloquear."""
        enabled = self._unlock_switch_var.get() == "on"

        with self._data_lock:
            password_hash = self.settings.unlock_password_hash

        if enabled and not password_hash:
            self._unlock_switch_var.set("off")
            self._notify_security_error("Defina uma senha antes de ativar a dificuldade.")
            return

        with self._data_lock:
            self.settings.unlock_difficulty_enabled = enabled

        self._save_config()

    def on_close(self) -> None:
        """Minimiza para bandeja ao fechar a janela."""
        if self._is_closing:
            return
        self.hide_to_tray()

    def exit_app(self) -> None:
        """Encerra app, threads de monitoramento e ícone de bandeja."""
        if self._is_closing:
            return

        if self.monitoring_active and not self._request_unlock_challenge():
            return

        self._is_closing = True
        self.stop_monitoring(bypass_unlock=True)
        self._stop_tray_icon()
        self.destroy()


if __name__ == "__main__":
    app = FocusGuardApp()
    app.mainloop()
