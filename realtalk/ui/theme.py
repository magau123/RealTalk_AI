"""界面配色与样式表。"""

from __future__ import annotations

BACKGROUND = "#ffffff"
SURFACE = "#f7f9fc"
SURFACE_RAISED = "#eef3f8"
BORDER = "#d9e2ec"
TEXT_PRIMARY = "#111827"
TEXT_SECONDARY = "#4b5563"
TEXT_MUTED = "#7c8797"
ACCENT = "#2563eb"
ACCENT_HOVER = "#3b82f6"
ACCENT_PRESSED = "#1d4ed8"
DANGER = "#dc2626"
SUCCESS = "#16865c"
WARNING = "#b45309"

STYLESHEET = f"""
QWidget {{
    background-color: {BACKGROUND};
    color: {TEXT_PRIMARY};
    font-family: "Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", sans-serif;
    font-size: 14px;
}}

QToolTip {{
    color: {TEXT_PRIMARY};
    background-color: {SURFACE_RAISED};
    border: 1px solid {BORDER};
    padding: 6px 8px;
}}

QLabel#AppTitle {{
    color: {TEXT_PRIMARY};
    font-size: 24px;
    font-weight: 700;
}}

QLabel#AppSubtitle {{
    color: {TEXT_MUTED};
    font-size: 12px;
}}

QLabel#OpacityLabel {{
    color: {TEXT_SECONDARY};
    font-size: 12px;
}}

QFrame#SettingsPanel {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}

QLabel#SectionTitle {{
    font-size: 11px;
    font-weight: 600;
    color: {TEXT_MUTED};
}}

QLabel#StatusLabel {{
    color: {TEXT_SECONDARY};
    font-size: 12px;
    padding: 2px 4px;
    background: transparent;
}}

QLabel#HintLabel {{
    color: {TEXT_MUTED};
    font-size: 13px;
    line-height: 1.5;
}}

QPushButton {{
    background-color: {SURFACE_RAISED};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 9px 16px;
    font-size: 13px;
}}

QPushButton:hover {{
    background-color: #e7edf5;
    border-color: #b8c6d8;
}}

QPushButton:disabled {{
    color: {TEXT_MUTED};
    background-color: {SURFACE};
}}

QPushButton#QuietButton {{
    background-color: transparent;
    border-color: transparent;
    color: {TEXT_MUTED};
    padding-left: 12px;
    padding-right: 12px;
}}

QPushButton#QuietButton:hover {{
    color: {TEXT_PRIMARY};
    background-color: {SURFACE};
    border-color: {BORDER};
}}

QComboBox {{
    background-color: {SURFACE_RAISED};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 11px;
    min-width: 110px;
}}

QComboBox:hover {{
    border-color: {ACCENT};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QComboBox QAbstractItemView {{
    background-color: {SURFACE_RAISED};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
    outline: none;
    padding: 5px;
}}

QScrollArea {{
    border: 1px solid {BORDER};
    border-radius: 12px;
    background-color: {SURFACE};
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px 2px 4px 2px;
}}

QScrollBar::handle:vertical {{
    background: #c5cfdb;
    border-radius: 5px;
    min-height: 30px;
}}

QScrollBar::handle:vertical:hover {{
    background: #aab8c8;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}

QFrame#ForeignBubble {{
    background-color: {SURFACE_RAISED};
    border: none;
    border-left: 3px solid {TEXT_MUTED};
    border-radius: 8px;
}}

QFrame#ForeignBubbleLive {{
    background-color: {SURFACE_RAISED};
    border: none;
    border-left: 3px solid {ACCENT};
    border-radius: 8px;
}}

QFrame#MyBubble {{
    background-color: #eef5ff;
    border: none;
    border-left: 3px solid {ACCENT};
    border-radius: 8px;
}}

QFrame#MyBubbleLive {{
    background-color: #eef5ff;
    border: none;
    border-left: 3px solid {ACCENT_HOVER};
    border-radius: 8px;
}}

QFrame#MyBubbleSpeaking {{
    background-color: #eef5ff;
    border: none;
    border-left: 3px solid {SUCCESS};
    border-radius: 8px;
}}

QPushButton#ListenButton, QPushButton#TurnButton {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 12px 18px;
    font-size: 14px;
    font-weight: 600;
}}

QPushButton#ListenButton:hover, QPushButton#TurnButton:hover {{
    border-color: {ACCENT};
    background-color: {SURFACE_RAISED};
}}

QPushButton#TurnButtonActive {{
    background-color: {ACCENT};
    border: none;
    border-radius: 10px;
    padding: 12px 18px;
    font-size: 14px;
    font-weight: 600;
    color: #ffffff;
}}

QPushButton#ListenButtonActive {{
    background-color: #eaf8f2;
    border: 1px solid #9bd8c0;
    border-radius: 10px;
    padding: 12px 18px;
    font-size: 14px;
    font-weight: 600;
    color: #116747;
}}

QPushButton#ListenButtonActive:hover {{
    background-color: #dcf3e9;
    border-color: {SUCCESS};
}}

QPushButton#ReplayButton {{
    background: transparent;
    border: none;
    color: {TEXT_MUTED};
    font-size: 12px;
    padding: 2px 6px;
}}

QPushButton#ReplayButton:hover {{
    color: {ACCENT};
}}

QLabel#SourceText {{
    color: {TEXT_SECONDARY};
    font-size: 14px;
}}

QLabel#TargetText {{
    color: {TEXT_PRIMARY};
    font-size: 17px;
}}

QLabel#CardMeta {{
    color: {TEXT_MUTED};
    font-size: 11px;
}}

QSlider#OpacitySlider::groove:horizontal {{
    height: 4px;
    background: {BORDER};
    border-radius: 2px;
}}

QSlider#OpacitySlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}

QSlider#OpacitySlider::handle:horizontal {{
    width: 14px;
    height: 14px;
    margin: -5px 0;
    background: #ffffff;
    border: 2px solid {ACCENT};
    border-radius: 7px;
}}

QSlider#OpacitySlider::handle:horizontal:hover {{
    background: #eff6ff;
}}
"""
