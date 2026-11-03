"""Register addresses per block listed by the manufacturer documents (requirement-10).

Register facts only: wire addresses (0-based), no names, no document text. The documents
themselves live in the homelab DEV docs and are never committed.

- STIEBEL_WPE_I_071: Stiebel doc 372257-47978-0071 (Modbus WPE-I 07.1-17.1 Plus, 11/2025),
  via its homelab transcription wpe-i-07.1-plus-modbus.yaml. Model level.
- THERMIA_GENESIS_17_1: Thermia "Modbus protocol for Genesis platform 17.1" (ACMBDH01UG0402),
  address list and function chapters, extracted 2026-10-24. Platform level.
"""

STIEBEL_WPE_I_071 = {
    "coil": frozenset({
        3, 5, 7, 8, 9, 10, 11, 16, 17, 20, 21, 22, 28, 31, 33, 34, 35, 37, 39, 41, 42, 43, 46, 59
    }),
    "dinput": frozenset({
        0, 1, 2, 3, 9, 10, 12, 14, 15, 16, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35,
        36, 37, 38, 46, 47, 48, 49, 50, 56, 57, 60, 61, 62, 66, 67, 68, 70, 74, 75, 76, 77, 83, 84,
        85, 87, 201, 202, 204, 210, 218, 219, 221, 247, 248, 249, 250, 251, 252, 253, 254
    }),
    "input": frozenset({
        1, 2, 4, 5, 6, 8, 9, 10, 11, 12, 13, 15, 16, 17, 18, 19, 30, 36, 39, 40, 41, 43, 44, 48, 49,
        50, 51, 52, 53, 54, 61, 67, 68, 99, 100, 104, 106, 119, 120, 121, 125, 126, 127, 128, 129,
        130, 131, 143, 145, 147, 148, 149, 160, 161, 162, 163, 311, 312, 313, 315, 319, 413
    }),
    "holding": frozenset({
        3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 16, 22, 23, 58, 61, 62, 67, 68, 69, 70, 76, 77, 78, 79, 80,
        81, 82, 85, 86, 87, 88, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117,
        118, 124, 125, 199, 200, 201, 202, 203, 204, 205, 206, 207, 208, 209, 210, 211, 212, 213,
        214, 215, 216, 298, 299, 300, 302, 303, 304, 305, 306, 307, 308, 309, 310, 311, 312
    }),
}

THERMIA_GENESIS_17_1 = {
    "coil": frozenset({
        3, 5, 6, 7, 8, 9, 10, 11, 14, 20, 21, 22, 25, 26, 28, 31, 33, 34, 35, 37, 38, 39, 40, 41,
        42, 59, 60, 61
    }),
    "dinput": frozenset({
        0, 1, 2, 3, 9, 10, 11, 12, 13, 14, 15, 16, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33,
        34, 35, 36, 47, 49, 50, 52, 55, 56, 57, 60, 66, 67, 74, 75, 76, 77, 78, 79, 83, 84, 85, 86,
        87, 199, 201, 202, 204, 206, 209, 210, 211, 218, 219, 220, 221, 223, 224, 225, 235, 236,
        238, 240, 242, 245, 246
    }),
    "input": frozenset({
        1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27,
        30, 36, 39, 40, 41, 43, 44, 45, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 60, 61, 62, 63, 64,
        65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 106, 119,
        120, 121, 122, 123, 124, 125, 126, 127, 128, 129, 130, 131, 137, 139, 140, 141, 142, 143,
        145, 146, 147, 157, 158, 160, 161, 162, 163, 311, 312, 313, 315, 319
    }),
    "holding": frozenset({
        0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 16, 22, 23, 26, 27, 28, 29, 30, 49, 50, 58, 61, 62, 63,
        67, 68, 69, 70, 75, 76, 77, 78, 79, 80, 81, 82, 85, 86, 87, 88, 105, 106, 107, 108, 109,
        110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 124, 125, 298, 299, 300, 302, 303,
        304, 321
    }),
}

# Unit column per block, as {source unit: addresses} (requirement-27). The source unit is kept verbatim
# (None where the source states none); tests/test_registers.py maps it onto the declared unit.
# Stiebel: every documented address. Thermia: the addresses the WPE-I declares platform_doc, each row
# checked against the address list (the text extraction of the other rows is not reviewed).
STIEBEL_WPE_I_071_UNITS = {
    "coil": {
        None: frozenset({
            3, 5, 7, 8, 9, 10, 11, 16, 17, 20, 21, 22, 28, 31, 33, 34, 35, 37, 39, 41, 42, 43, 46,
            59
        }),
    },
    "dinput": {
        None: frozenset({
            0, 1, 2, 3, 9, 10, 12, 14, 15, 16, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34,
            35, 36, 37, 38, 46, 47, 48, 49, 50, 56, 57, 60, 61, 62, 66, 67, 68, 70, 74, 75, 76, 77,
            83, 84, 85, 87, 201, 202, 204, 210, 218, 219, 221, 247, 248, 249, 250, 251, 252, 253,
            254
        }),
    },
    "input": {
        None: frozenset({
            1, 2, 4, 30, 36, 61, 67, 131, 143, 145, 160, 161, 162, 163, 311, 312, 313, 413
        }),
        "%": frozenset({6, 39, 43, 44, 54, 315}),
        "K": frozenset({125, 126}),
        "bar": frozenset({127, 128}),
        "h": frozenset({48, 49, 50, 51, 52, 53}),
        "rpm": frozenset({5}),
        "°C": frozenset({
            8, 9, 10, 11, 12, 13, 15, 16, 17, 18, 19, 40, 41, 68, 99, 100, 104, 106, 119, 120, 121,
            129, 130, 147, 148, 149, 319
        }),
    },
    "holding": {
        None: frozenset({78, 85, 86, 87, 88, 117, 124, 298, 305, 309}),
        "%": frozenset({76, 77, 79, 80, 81, 82}),
        "K": frozenset({5, 70, 300}),
        "kW": frozenset({125}),
        "s": frozenset({61, 62}),
        "°C": frozenset({
            3, 4, 6, 7, 8, 9, 10, 11, 12, 16, 22, 23, 58, 67, 68, 69, 105, 106, 107, 108, 109, 110,
            111, 112, 113, 114, 115, 116, 118, 199, 200, 201, 202, 203, 204, 205, 206, 207, 208,
            209, 210, 211, 212, 213, 214, 215, 216, 299, 302, 303, 304, 306, 307, 308, 310, 311, 312
        }),
    },
}

THERMIA_GENESIS_17_1_UNITS = {
    "coil": {
        "on/off": frozenset({6, 14, 25, 26, 38, 40}),
    },
    "dinput": {
        "active/inactive": frozenset({11, 13, 52, 55}),
        "on/off": frozenset({
            78, 79, 86, 199, 206, 209, 211, 220, 223, 224, 225, 235, 236, 238, 240, 242, 245, 246
        }),
    },
    "input": {
        None: frozenset({60, 83, 139, 140, 141, 142, 146}),
        "%": frozenset({47, 137}),
        "A": frozenset({69, 70, 71}),
        "V": frozenset({72, 73, 74, 75, 76, 77}),
        "W": frozenset({78, 79, 80}),
        "enum": frozenset({55, 56, 62, 63, 64, 65, 66, 82}),
        "kW": frozenset({157, 158}),
        "kWh": frozenset({81}),
        "°C": frozenset({7, 20, 21, 22, 23, 24, 25, 26, 45, 122, 124}),
    },
    "holding": {
        None: frozenset({26, 27, 28, 29, 75}),
        "%": frozenset({30, 49, 50}),
        "A": frozenset({119, 120}),
        "enum": frozenset({0, 321}),
        "min": frozenset({63}),
    },
}

# Enumeration rows per block (requirement-32, ac-123): the addresses whose type column reads
# `Aufzählung` (Stiebel) or `enum` (Thermia), each row checked against the source text. Stiebel
# holding 309 (heating circuit 4 mode) has an empty type column and is not listed.
STIEBEL_WPE_I_071_ENUMS = {
    "input": frozenset({413}),
    "holding": frozenset({117, 124, 298, 305}),
}

THERMIA_GENESIS_17_1_ENUMS = {
    "input": frozenset({1, 55, 56, 62, 63, 64, 65, 66, 82}),
    "holding": frozenset({0, 117, 124, 298, 321}),
}

# Source corrections (requirement-27, ac-106): (block, address) -> the source unit overridden, the
# declared unit and the evidence. Each needs an owner decision.
UNIT_CORRECTIONS = {
    ("holding", 5): {
        "source_unit": "K",
        "unit": "°C",
        "evidence": "Stiebel doc 372257-47978-0071 row 40006 names Raumtemperatur Sollwert"
                    " (room temperature set point); fixtures 6.0.6 and 6.1.5 both read 20.5."
                    " Owner decision 2026-11-03 (issue-32): absolute temperature, not a delta.",
    },
    ("holding", 63): {
        "source_unit": "min",
        "unit": "s",
        "evidence": "Stiebel gives s for the sibling gear shift delays holding 61 and 62; probes of"
                    " 6.0.6 and 6.1.5 read 300, 300, 60 for holdings 61-63 (owner decision 2026-10-31,"
                    " action_item-1)",
    },
}

# Display labels of the WPE-I (requirement-28, ac-110): (block, address) -> the English label, the
# German row text (name_de, Stiebel rows only; the row's FC access note dropped) and the source row.
# Each label translates its source row by the owner's rule (2026-10-29): Vorlauf -> supply,
# Rücklauf -> return, Solltemperatur / Sollwert -> set temperature, Heizkreis / HK -> heating circuit.
# Stiebel rows cite section and de facto address; discrete input 209 has no Stiebel row, so it cites
# Thermia Genesis 17.1, where "Mix valve 1" is the circuit Stiebel calls Heizkreis 2 (section 2.3).
STIEBEL = "Stiebel doc 372257-47978-0071"
THERMIA = "Thermia Genesis 17.1"
# Heating circuit 2 curve, holdings 109-114: outdoor temperature [°C] per address; 115 has a longer row.
_HC2_CURVE_OUTDOOR = {109: "17", 110: "10", 111: "5", 112: "0", 113: "-5", 114: "-15"}
WPE_I_071_LABELS = {
    ("input", 18): {
        "name": "Calculated system supply set temperature (T33)",
        "name_de": "Berechneter Sollwert Systemzulauftemperatur auf T33",
        "source": f"{STIEBEL} 2.2, 30019",
    },
    ("input", 40): {
        "name": "Heating circuit 2 supply temperature (T32)",
        "name_de": "Zulauftemperatur Heizkreis 2 T32",
        "source": f"{STIEBEL} 2.3, 30041",
    },
    ("input", 43): {
        "name": "Heating circuit 2 mixing valve control",
        "name_de": "Ansteuerung Mischventil Heizkreis 2",
        "source": f"{STIEBEL} 2.3, 30044",
    },
    ("input", 147): {
        "name": "Heating/cooling circuit 2 supply set temperature",
        "name_de": "Solltemperatur HK/Kühlkreis 2",
        "source": f"{STIEBEL} 2.3, 30148",
    },
    ("dinput", 36): {
        "name": "Alarm: heating circuit 2 supply sensor",
        "name_de": "Alarm Zulaufleitungsfühler Heizkreis 2",
        "source": f"{STIEBEL} 2.21, 10037",
    },
    ("dinput", 60): {
        "name": "Alarm: heating circuit 2 supply temperature deviation",
        "name_de": "Alarm Abweichung Vorlauftemperatur Heizkreis 2",
        "source": f"{STIEBEL} 2.21, 10061",
    },
    ("dinput", 209): {
        "name": "Heating circuit 2 circulation pump control signal",
        "name_de": None,
        "source": f"{THERMIA} 5.2 Mix valve 1, 10210: Mix valve 1 circulation pump control signal (EM)",
    },
    **{
        ("holding", address): {
            "name": f"Heating circuit 2 supply set temperature at {outdoor} °C outdoor",
            "name_de": f"Vorlaufsolltemperatur HK2 bei {outdoor}°C AT",
            "source": f"{STIEBEL} 2.3, {40001 + address}",
        }
        for address, outdoor in _HC2_CURVE_OUTDOOR.items()
    },
    ("holding", 115): {
        "name": "Heating circuit 2 supply set temperature at -25 °C outdoor",
        "name_de": "Sollwertkurve, Y-Koordinate 7 Heizkreis 2 (niedrigste Außentemperatur)"
                   " Vorlaufsolltemperatur HK2 bei -25°C AT",
        "source": f"{STIEBEL} 2.3, 40116",
    },
}
