"""A small multi-module codebase with the two hazards deliberately present:
a shadowed short name, and an import cycle."""

CORPUS = {
    "billing.money": (
        "def parse(raw):\n"
        "    '''Parse a currency string into integer cents.'''\n"
        "    return int(round(float(raw) * 100))\n"
        "\n"
        "def format_cents(cents):\n"
        "    return f'${cents / 100:,.2f}'\n"
    ),
    "billing.invoice": (
        "from billing.money import parse, format_cents\n"
        "from billing.tax import apply_tax\n"
        "\n"
        "def build_line(raw_amount, rate):\n"
        "    cents = parse(raw_amount)\n"
        "    return apply_tax(cents, rate)\n"
        "\n"
        "def render(cents):\n"
        "    return format_cents(cents)\n"
    ),
    "billing.tax": (
        "def apply_tax(cents, rate):\n"
        "    return cents + round(cents * rate)\n"
    ),
    "ingest.dates": (
        "def parse(raw):\n"
        "    '''Parse an ISO date. Unrelated to billing.money.parse.'''\n"
        "    y, m, d = raw.split('-')\n"
        "    return (int(y), int(m), int(d))\n"
    ),
    "ingest.loader": (
        "from ingest.dates import parse\n"
        "from ingest.normalise import clean\n"
        "\n"
        "def load_row(row):\n"
        "    when = parse(row['date'])\n"
        "    return clean(row, when)\n"
    ),
    "ingest.normalise": (
        "def clean(row, when):\n"
        "    return {'when': when, **row}\n"
    ),
    "reporting.summary": (
        "from billing.invoice import build_line, render\n"
        "from ingest.loader import load_row\n"
        "\n"
        "def monthly(rows, rate):\n"
        "    total = 0\n"
        "    for row in rows:\n"
        "        loaded = load_row(row)\n"
        "        total += build_line(loaded['amount'], rate)\n"
        "    return render(total)\n"
    ),
    "legacy.alpha": (
        "import legacy.beta\n"
        "def start():\n"
        "    return legacy.beta.middle()\n"
    ),
    "legacy.beta": (
        "import legacy.gamma\n"
        "def middle():\n"
        "    return legacy.gamma.finish()\n"
    ),
    "legacy.gamma": (
        "import legacy.alpha\n"
        "def finish():\n"
        "    return legacy.alpha.start()\n"
    ),
}
