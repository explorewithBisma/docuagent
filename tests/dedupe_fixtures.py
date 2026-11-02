"""
A SYNTHETIC customer list with planted near-duplicates and tricky look-alikes (invented people, phones and emails), for the
near-duplicate tests. It comes with GROUND TRUTH: which rows are the same record, and which rows only look similar.

  customers(n)  -> (DataFrame, Truth)

Columns: Customer ID (a different value on every row - a row key), Full Name, Email, Mobile, National ID, Company, Address, City,
Signup Date, Total Spend, Status. Dates, amounts and status DIFFER between a person's rows on purpose.
"""

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

FIRSTS = ["Ali", "Sara", "Omar", "Layla", "Hassan", "Nour", "Zainab", "Karim", "Dina", "Faisal", "Hana", "Imran", "Jamal", "Maryam", "Nabil",
          "Rania", "Salim", "Tariq", "Yasmin", "Zeeshan", "Adnan", "Bilal", "Hira", "Kamran", "Noman", "Saad", "Uzma", "Waqar", "Anwar", "Sana"]
LASTS = ["Khan", "Raza", "Malik", "Sheikh", "Qureshi", "Ansari", "Butt", "Chaudhry", "Dar", "Farooq", "Gill", "Hashmi", "Iqbal", "Javed",
         "Latif", "Mirza", "Naqvi", "Rehman", "Siddiqui", "Tahir", "Usmani", "Virk", "Yousaf", "Zaidi", "Abbasi", "Baig", "Cheema", "Durrani",
         "Ejaz", "Fazal"]
COMPANIES = ["Acme Trading LLC", "Blue Nile Foods", "City Pharmacy", "Delta Logistics", "Emirates Tech", "Falcon Stores", "Gulf Traders",
             "Horizon Mart", "Indus Textiles", "Jade Interiors"]
CITIES = ["Dubai", "Karachi", "Lahore", "Abu Dhabi", "Sharjah", "Islamabad", "Doha", "Riyadh", "Cairo", "Amman"]
STREETS = ["Main Street", "Park Road", "Garden Avenue", "Station Road", "Lake Lane", "Hill Street", "Market Road", "Canal Avenue"]


RESERVED = {("Ali", "Khan"), ("Sara", "Raza"), ("Omar", "Farooq"), ("Hassan", "Malik"), ("Layla", "Ansari"), ("Karim", "Dar"), ("Rania", "Javed"),
            ("Imran", "Latif"), ("Nabil", "Chaudhry"), ("Tariq", "Mirza"), ("Bilal", "Tahir"), ("Saad", "Butt"), ("Hira", "Naqvi"), ("Anwar", "Cheema"), ("Ahmed", "Ali"), ("Ali", "Ahmed"),
            ("Sana", "Chaudhry"), ("Zeeshan", "Chaudhry")}


@dataclass
class Truth:
    groups: list = field(default_factory=list)        # [{"rows": [positions], "kind": str, "level": "high" | "maybe"}]
    lookalikes: list = field(default_factory=list)    # [{"rows": [positions], "kind": str}]: must NEVER share a group

    def pairs(self, level=None) -> set:
        out = set()
        for group in self.groups:
            if level is None or group["level"] == level:
                rows = sorted(group["rows"])
                out |= {(a, b) for i, a in enumerate(rows) for b in rows[i + 1:]}
        return out

    def lookalike_pairs(self) -> set:
        out = set()
        for group in self.lookalikes:
            rows = sorted(group["rows"])
            out |= {(a, b) for i, a in enumerate(rows) for b in rows[i + 1:]}
        return out


def _phone(rng: random.Random, taken: set) -> str:
    while True:
        number = f"03{rng.randint(0, 4)}{rng.randint(0, 9)}{rng.randint(1000000, 9999999)}"
        if number not in taken:
            taken.add(number)
            return number


DOB_RESERVED = {("Zainab", "Usmani"), ("Nour", "Gill"), ("Kamran", "Abbasi"), ("Uzma", "Baig"), ("Waqar", "Virk"), ("Maryam", "Zaidi"), ("Jamal", "Dar")}


def customers(n: int = 300, seed: int = 7, plants: int = 1, with_dob: bool = False):
    """n base customers (+ planted duplicates and look-alikes, `plants` times the standard set). Returns (DataFrame, Truth).
    with_dob adds "Date of Birth" and "Postcode" columns and the plants that rest on them (the default list is unchanged)."""
    rng = random.Random(seed)
    dob_rng = random.Random(seed + 1)                              # a separate stream: the default list is the same with or without it
    reserved = RESERVED | DOB_RESERVED if with_dob else RESERVED
    phones, ids, emails = set(), set(), set()

    def person(first, last, key):
        email = f"{first}.{last}.{key}@{rng.choice(['gmail.com', 'outlook.com', 'work.ae', 'mail.pk'])}".lower()
        emails.add(email)
        national = f"{rng.randint(10000, 99999)}{rng.randint(1000000, 9999999)}{rng.randint(1, 9)}"
        ids.add(national)
        return {"Customer ID": None, "Full Name": f"{first} {last}", "Email": email, "Mobile": _phone(rng, phones), "National ID": national,
                "Company": rng.choice(COMPANIES), "Address": f"{rng.randint(1, 99)} {rng.choice(STREETS)}", "City": rng.choice(CITIES),
                "Signup Date": date(2023, 1, 1) + timedelta(days=rng.randint(0, 900)), "Total Spend": round(rng.uniform(20, 5000), 2),
                "Status": rng.choice(["Active", "Active", "Active", "Dormant", "VIP"]),
                **({"Date of Birth": (date(1950, 1, 1) + timedelta(days=dob_rng.randint(0, 20000))).isoformat(),
                    "Postcode": str(dob_rng.randint(10000, 99999))} if with_dob else {})}

    rows = []
    for i in range(n):
        while True:                                                    # random people never share a name with a planted one
            first, last = rng.choice(FIRSTS), rng.choice(LASTS)
            if (first, last) not in reserved:
                break
        rows.append(person(first, last, i))
    truth = Truth()

    def add(base_position, **changes):
        """A new row that repeats the person at `base_position` with some fields changed; dates/amounts/status differ legitimately."""
        row = dict(rows[base_position])
        row.update({"Signup Date": row["Signup Date"] + timedelta(days=rng.randint(1, 200)), "Total Spend": round(rng.uniform(20, 5000), 2),
                    "Status": rng.choice(["Active", "Dormant"])})
        row.update(changes)
        rows.append(row)
        return len(rows) - 1

    def special(first, last):
        rows.append(person(first, last, len(rows)))
        return len(rows) - 1

    for _ in range(plants):
        # --- true duplicates -----------------------------------------------------------------------------------------
        b = special("Ali", "Khan");        d = add(b, **{"Full Name": "ali  khan.", "National ID": None, "Company": None});                                     truth.groups.append({"rows": [b, d], "kind": "case_space_punct", "level": "high"})
        b = special("Sara", "Raza");       d = add(b, Mobile=f"+92 {rows[b]['Mobile'][1:4]} {rows[b]['Mobile'][4:]}", Email=None, **{"National ID": None, "Address": None})
        truth.groups.append({"rows": [b, d], "kind": "phone_format", "level": "high"})
        b = special("Omar", "Farooq");     d = add(b, Email=rows[b]["Email"].upper(), Mobile=None, **{"National ID": None, "Company": None});                       truth.groups.append({"rows": [b, d], "kind": "email_case", "level": "high"})
        b = special("Hassan", "Malik");    d = add(b, **{"Full Name": "Hassan Mailk", "National ID": None, "Company": None, "Address": None});                                   truth.groups.append({"rows": [b, d], "kind": "typo", "level": "high"})
        b = special("Muhammad", "Siddiqui"); d = add(b, **{"Full Name": "Mohammad Siddiqui", "National ID": None});                            truth.groups.append({"rows": [b, d], "kind": "variant", "level": "high"})
        b = special("Mohamed", "Qureshi"); d = add(b, **{"Full Name": "M. Qureshi", "National ID": None, "Company": None}, Mobile=None);                        truth.groups.append({"rows": [b, d], "kind": "initial", "level": "high"})
        b = special("Layla", "Ansari");    d = add(b, **{"Full Name": "Ansari, Layla", "National ID": None, "Address": None}, Email=None);                      truth.groups.append({"rows": [b, d], "kind": "word_order", "level": "high"})
        b = special("Karim", "Dar");       d1 = add(b, **{"Full Name": "KARIM DAR", "National ID": None}, Mobile="+92" + rows[b]["Mobile"][1:]); d2 = add(b, Email=rows[b]["Email"].title(), Mobile=None, **{"National ID": None, "Company": None})
        truth.groups.append({"rows": [b, d1, d2], "kind": "triple", "level": "high"})
        b = special("Fatima", "Gill");     d = add(b, **{"Full Name": "Fatimah Gill"}, Email=None, Mobile=None, **{"National ID": rows[b]["National ID"]})
        truth.groups.append({"rows": [b, d], "kind": "id_and_variant", "level": "high"})
        b = special("Yousef", "Hashmi");   d = add(b, **{"Full Name": "yousef hashmi"}, Email=None, Mobile=None, **{"National ID": None},
                                                    Company=rows[b]["Company"].upper(), Address=rows[b]["Address"].lower().replace("street", "st"))
        truth.groups.append({"rows": [b, d], "kind": "name_company_address", "level": "high"})
        b = special("Rania", "Javed");     d = add(b, **{"Full Name": "rania javed"}, Email=None, Mobile=None, **{"National ID": None}, Company=None, Address=None)
        truth.groups.append({"rows": [b, d], "kind": "name_only", "level": "maybe"})

        b = special("Anwar", "Cheema");    d = add(b, Mobile=_phone(rng, phones), **{"National ID": None, "Company": None})
        truth.groups.append({"rows": [b, d], "kind": "two_phones", "level": "maybe"})

        # --- look-alikes that are NOT the same record --------------------------------------------------------------
        b = special("Imran", "Latif")
        d = add(b, Email="imran.latif.other@gmail.com", Mobile=_phone(rng, phones), **{"National ID": "99999000000019"},
                Company="Other Co", Address="1 Elsewhere Road")
        truth.lookalikes.append({"rows": [b, d], "kind": "same_common_name_different_person"})
        b = special("Nabil", "Chaudhry")
        d = add(b, **{"Full Name": "Sana Chaudhry"}, Email="sana.chaudhry.x@gmail.com", Mobile=_phone(rng, phones), **{"National ID": "99999000000027"})
        d2 = add(b, **{"Full Name": "Zeeshan Chaudhry"}, Email="zeeshan.chaudhry.x@gmail.com", Mobile=_phone(rng, phones), **{"National ID": "99999000000035"})
        truth.lookalikes.append({"rows": [b, d, d2], "kind": "family_same_address_surname"})
        b = special("Tariq", "Mirza")
        d = add(b, **{"Full Name": "Hira Naqvi"}, Email="hira.naqvi.x@gmail.com", **{"National ID": "99999000000043"})
        truth.lookalikes.append({"rows": [b, d], "kind": "shared_phone_different_names"})
        b = special("Bilal", "Tahir")
        d = add(b, **{"National ID": "11111000000011"})
        truth.lookalikes.append({"rows": [b, d], "kind": "same_name_same_phone_different_id"})
        b = special("Ahmed", "Ali")
        d = add(b, **{"Full Name": "Ali Ahmed"}, Email="ali.ahmed.x@gmail.com", Mobile=_phone(rng, phones), **{"National ID": None})
        truth.lookalikes.append({"rows": [b, d], "kind": "two_people_with_swapped_names"})
        b = special("Saad", "Butt")
        d = add(b, Email="saad.butt.x@gmail.com", Mobile=_phone(rng, phones), **{"National ID": None, "Company": "Another Trading", "Address": "5 Far Street"})
        truth.lookalikes.append({"rows": [b, d], "kind": "same_name_nothing_else_in_common"})

        if with_dob:
            # --- a date of birth is the only thing that confirms these (a Maybe, never a High) ---------------------------
            nothing = dict(Email=None, Mobile=None, Company=None, Address=None, **{"National ID": None})
            b = special("Zainab", "Usmani"); d = add(b, **{"Full Name": "Zainab Usmnai"}, **nothing)
            truth.groups.append({"rows": [b, d], "kind": "dob_name_typo", "level": "maybe"})
            b = special("Nour", "Gill");     d = add(b, **{"Full Name": None}, **nothing)
            truth.groups.append({"rows": [b, d], "kind": "dob_postcode_no_name", "level": "maybe"})
            # --- look-alikes -------------------------------------------------------------------------------------------
            b = special("Kamran", "Abbasi"); d = add(b, **{"Full Name": "Uzma Baig"}, **nothing)
            truth.lookalikes.append({"rows": [b, d], "kind": "same_birthday_same_postcode_different_people"})
            b = special("Waqar", "Virk")
            d = add(b, **nothing, **{"Date of Birth": "1999-09-09", "Postcode": "11111"})
            truth.lookalikes.append({"rows": [b, d], "kind": "same_name_different_birthday"})
            b = special("Maryam", "Zaidi"); d = add(b, **{"Full Name": None}, **nothing, **{"Postcode": "22222"})
            truth.lookalikes.append({"rows": [b, d], "kind": "same_birthday_no_name_other_postcode"})
            b = special("Jamal", "Dar");     d = add(b, **{"Full Name": "Jamal Dar"}, Email="jamal.other@gmail.com", Mobile=_phone(rng, phones),
                                                       **{"National ID": None, "Date of Birth": "1960-06-06"})
            truth.lookalikes.append({"rows": [b, d], "kind": "same_name_other_phone_other_birthday"})

    frame = pd.DataFrame(rows)
    frame["Customer ID"] = [f"C{i + 1:05d}" for i in range(len(frame))]
    return frame, truth


def inflate(frame: pd.DataFrame, truth: Truth, count: int, seed: int = 3):
    """Append `count` simple duplicates of random rows (case/spacing, phone format, email case, a name typo, a missing field), each keeping
    a phone or an email so it is a HIGH duplicate. Returns (frame, truth) - the truth gains one group per duplicate."""
    rng = random.Random(seed)
    frame = frame.copy()
    base_count = len(frame)
    new_rows, groups = [], []
    eligible = [i for i in range(base_count) if i not in {r for g in truth.groups for r in g["rows"]}
                and i not in {r for g in truth.lookalikes for r in g["rows"]}]
    for k in range(count):
        position = rng.choice(eligible)
        row = frame.iloc[position].to_dict()
        kind = rng.choice(["case", "phone", "email", "typo", "spaces"])
        name = str(row["Full Name"])
        if kind == "case":
            row["Full Name"] = name.upper()
        elif kind == "phone":
            digits = str(row["Mobile"])
            row["Mobile"] = f"+92 {digits[1:4]}-{digits[4:]}"
            row["Email"] = None
        elif kind == "email":
            row["Email"] = str(row["Email"]).upper()
            row["Mobile"] = None
        elif kind == "typo":
            first, last = name.split(" ", 1)
            row["Full Name"] = f"{first} {last[1] + last[0] + last[2:]}" if len(last) > 3 else name
        else:
            row["Full Name"] = f"  {name.replace(' ', '   ')}. "
        row["National ID"], row["Company"] = None, None
        row["Signup Date"] = row["Signup Date"] + timedelta(days=rng.randint(1, 100))
        row["Total Spend"] = round(rng.uniform(20, 5000), 2)
        new_rows.append(row)
        groups.append((position, base_count + k, kind))
    merged_groups = {}                                                 # two duplicates of the same person are ONE group of three
    for position, new_position, kind in groups:
        merged_groups.setdefault(position, {"rows": [position], "kind": f"bulk_{kind}", "level": "high"})["rows"].append(new_position)
    groups = list(merged_groups.values())
    out = pd.concat([frame, pd.DataFrame(new_rows)], ignore_index=True)
    out["Customer ID"] = [f"C{i + 1:06d}" for i in range(len(out))]
    merged = Truth(groups=truth.groups + groups, lookalikes=truth.lookalikes)
    return out, merged


# 17 invented people + 7 near-duplicate rows = 24 rows. A duplicate keeps its phone or email (so the repeats are mostly pairs on Email), but
# its last order date and total spent differ - exactly what made the finder call the file "a list of transactions".
PAIR_PEOPLE = [("Ayesha Rahman", "0300-1112221", "ayesha.r@mail.test"), ("Bilal Chaudhry", "0301-1112222", "bilal.c@mail.test"),
          ("Cyrus Mansoor", "0302-1112223", "cyrus.m@mail.test"), ("Dania Sheikh", "0303-1112224", "dania.s@mail.test"),
          ("Ehsan Qadir", "0304-1112225", "ehsan.q@mail.test"), ("Farah Naqvi", "0305-1112226", "farah.n@mail.test"),
          ("Ghazi Mirza", "0306-1112227", "ghazi.m@mail.test"), ("Hina Baig", "0307-1112228", "hina.b@mail.test"),
          ("Idris Latif", "0308-1112229", "idris.l@mail.test"), ("Javeria Dar", "0309-1112230", "javeria.d@mail.test"),
          ("Kashif Ansari", "0310-1112231", "kashif.a@mail.test"), ("Lubna Gill", "0311-1112232", "lubna.g@mail.test"),
          ("Murtaza Hashmi", "0312-1112233", "murtaza.h@mail.test"), ("Nadia Virk", "0313-1112234", "nadia.v@mail.test"),
          ("Osama Zaidi", "0314-1112235", "osama.z@mail.test"), ("Parveen Cheema", "0315-1112236", "parveen.c@mail.test"),
          ("Qasim Abbasi", "0316-1112237", "qasim.a@mail.test")]
PAIR_CITIES = ["Lahore", "Karachi", "Dubai", "Doha", "Multan", "Sharjah"]
PAIR_DUPLICATES = {0: ("AYESHA  RAHMAN", None, "ayesha.r@mail.test"), 1: ("Bilal Chaudary", "03011112222", None), 2: ("Cyrus Mansoor.", "+92 302 1112223", "CYRUS.M@MAIL.TEST"),
              3: ("Dania Shaikh", None, "dania.s@mail.test"), 4: ("E. Qadir", "0304 1112225", None), 5: ("Naqvi, Farah", None, "farah.n@mail.test"),
              6: ("ghazi mirza", "0306-1112227", "ghazi.m@mail.test")}


def pair_customer_list():
    rows = []
    for i, (name, phone, email) in enumerate(PAIR_PEOPLE):
        rows.append({"Customer Name": name, "Phone": phone, "Email": email, "City": PAIR_CITIES[i % 6], "Date of Birth": date(1970 + i, 1 + i % 12, 1 + i),
                     "Last Order Date": date(2026, 1, 1) + timedelta(days=i * 3), "Total Spent": 100.0 + 37 * i})
    for k, (name, phone, email) in PAIR_DUPLICATES.items():
        base = rows[k]
        rows.append({**base, "Customer Name": name, "Phone": phone, "Email": email,
                     "Last Order Date": base["Last Order Date"] + timedelta(days=40 + k), "Total Spent": base["Total Spent"] + 55 + k})
    frame = pd.DataFrame(rows)
    return frame.sample(frac=1, random_state=4).reset_index(drop=True)             # duplicates are not next to their originals
