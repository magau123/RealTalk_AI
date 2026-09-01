"""界面配色与样式表。"""

from __future__ import annotations

BACKGROUND = "#12141a"
SURFACE = "#1b1e26"
SURFACE_RAISED = "#232733"
BORDER = "#2e3340"
TEXT_PRIMARY = "#e8eaf0"
TEXT_SECONDARY = "#9aa2b4"
TEXT_MUTED = "#6b7386"
ACCENT = "#4c8dff"
ACCENT_HOVER = "#6ba0ff"
ACCENT_PRESSED = "#3a72d8"
DANGER = "#ff5f57"
SUCCESS = "#3ddc84"
WARNING = "#ffb340"

STYLESHEET = f"""
QWidget {{
    background-color: {BACKGROUND};
    color: {TEXT_PRIMARY};
    font-family: "Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", sans-serif;
    font-size: 14px;
}}

QTabWidget::pane {{
    border: none;
    background-color: {BACKGROUND};
}}

QTabBar {{
    qproperty-drawBase: 0;
}}

QTabBar::tab {{
    background: transparent;
    color: {TEXT_SECONDARY};
    padding: 10px 22px;
    margin-right: 4px;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 15px;
}}

QTabBar::tab:selected {{
    color: {TEXT_PRIMARY};
    border-bottom: 2px solid {ACCENT};
}}

QTabBar::tab:hover:!selected {{
    color: {TEXT_PRIMARY};
}}

QLabel#SectionTitle {{
    font-size: 13px;
    color: {TEXT_MUTED};
}}

QLabel#StatusLabel {{
    color: {TEXT_SECONDARY};
    font-size: 13px;
    padding: 6px 10px;
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
}}

QLabel#HintLabel {{
    color: {TEXT_MUTED};
    font-size: 12px;
}}

QPushButton {{
    background-color: {SURFACE_RAISED};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 8px 18px;
    font-size: 14px;
}}

QPushButton:hover {{
    background-color: #2b3040;
}}

QPushButton:disabled {{
    color: {TEXT_MUTED};
    background-color: {SURFACE};
}}

QPushButton#PrimaryButton {{
    background-color: {ACCENT};
    border: none;
    color: #ffffff;
    font-weight: 600;
    padding: 9px 24px;
}}

QPushButton#PrimaryButton:hover {{
    background-color: {ACCENT_HOVER};
}}

QPushButton#PrimaryButton:pressed {{
    background-color: {ACCENT_PRESSED};
}}

QPushButton#PrimaryButton:disabled {{
    background-color: #2c3444;
    color: {TEXT_MUTED};
}}

QPushButton#DangerButton {{
    background-color: transparent;
    border: 1px solid {DANGER};
    color: {DANGER};
}}

QPushButton#DangerButton:hover {{
    background-color: rgba(255, 95, 87, 0.12);
}}

QComboBox {{
    background-color: {SURFACE_RAISED};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 7px 12px;
    min-width: 120px;
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
    outline: none;
    padding: 4px;
}}

QTextEdit, QPlainTextEdit {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 12px;
    font-size: 15px;
    selection-background-color: {ACCENT};
}}

QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {ACCENT};
}}

QScrollArea {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    background-color: {SURFACE};
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px 2px 4px 2px;
}}

QScrollBar::handle:vertical {{
    background: #39404f;
    border-radius: 5px;
    min-height: 30px;
}}

QScrollBar::handle:vertical:hover {{
    background: #475062;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}

/* 消息整行铺满，说话人靠左侧色条区分：对方灰、我蓝、朗读中绿。 */
QFrame#ForeignBubble {{
    background-color: {SURFACE_RAISED};
    border: 1px solid {BORDER};
    border-left: 4px solid {TEXT_MUTED};
    border-radius: 10px;
}}

QFrame#ForeignBubbleLive {{
    background-color: {SURFACE_RAISED};
    border: 1px solid {BORDER};
    border-left: 4px solid {ACCENT};
    border-radius: 10px;
}}

QFrame#MyBubble {{
    background-color: #1e2c47;
    border: 1px solid #2f4468;
    border-left: 4px solid {ACCENT};
    border-radius: 10px;
}}

QFrame#MyBubbleLive {{
    background-color: #1e2c47;
    border: 1px solid #2f4468;
    border-left: 4px solid {ACCENT_HOVER};
    border-radius: 10px;
}}

QFrame#MyBubbleSpeaking {{
    background-color: #1e2c47;
    border: 1px solid #2f4468;
    border-left: 4px solid {SUCCESS};
    border-radius: 10px;
}}

QPushButton#TurnButton {{
    background-color: {SURFACE_RAISED};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 16px 20px;
    font-size: 16px;
    font-weight: 600;
}}

QPushButton#TurnButton:hover {{
    border-color: {ACCENT};
}}

QPushButton#TurnButtonActive {{
    background-color: {ACCENT};
    border: 1px solid {ACCENT};
    border-radius: 10px;
    padding: 16px 20px;
    font-size: 16px;
    font-weight: 600;
    color: #ffffff;
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
"""
