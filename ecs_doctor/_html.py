from html import escape


def html_select_options(values: list[str], placeholder: str) -> str:
    """Build <option> tags with HTML-escaped values and labels."""
    lines = [f'<option value="" disabled selected>{escape(placeholder)}</option>']
    for value in values:
        safe = escape(value, quote=True)
        lines.append(f'<option value="{safe}">{safe}</option>')
    return "\n".join(lines)
