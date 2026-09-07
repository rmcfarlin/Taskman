"""Shared, visibly editable compact controls for Taskman modal dialogs."""

COMPACT_DIALOG_CSS = """
.compact-dialog Input {
    height: 1; min-height: 1; border: none; padding: 0 1;
    background: $panel; background-tint: transparent;
}
.compact-dialog Input:focus {
    border: none; background: $primary 20%; background-tint: transparent;
    text-style: underline;
}
.compact-dialog Button.-style-default {
    height: 1; min-height: 1; min-width: 8; width: auto;
    border: none !important; padding: 0 1; background: $panel;
    color: $text; text-style: bold;
}
.compact-dialog Button.-style-default.-primary { background: $primary 20%; color: $text; }
.compact-dialog Button.-style-default.-error { background: $error 20%; color: $text; }
.compact-dialog Button.-style-default:hover { background: $primary 25%; }
.compact-dialog Button.-style-default:focus {
    background: $primary 30%; text-style: bold underline;
}
.compact-dialog Button.-style-default:disabled { background: $panel; color: $text-disabled; }
"""
