"""Inline sample set (AC #1, #3): positives as (text, type, identifier substring) and
negatives as (text, type that must not be reported). The full suite arrives in 4.7."""

from typing import Final

from app.guardrails.identifiers.types import IdentifierType as T
from tests.guardrails.identifiers.conftest import (
    group,
    luhn_complete,
    luhn_invalid,
    verhoeff_complete,
    verhoeff_invalid,
)


def to_devanagari(digits: str) -> str:
    return digits.translate(str.maketrans("0123456789", "०१२३४५६७८९"))


def to_fullwidth(text: str) -> str:
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in text)


A = [
    verhoeff_complete(b)
    for b in (
        "23456789012",
        "34567890123",
        "98765432101",
        "54321098765",
        "87654321098",
        "29384756102",
        "61728394051",
        "70192837465",
    )
]
VISA = luhn_complete("411111111111111")
MASTER = luhn_complete("550000000000000")
AMEX = luhn_complete("37828224631000")
DINERS = luhn_complete("3056930902590")
RUPAY = luhn_complete("608032104567890")
VISA13 = luhn_complete("422222222222")
CARD19 = luhn_complete("622222222222222222")

Positive = tuple[str, T, str]
Negative = tuple[str, T]


def _pos(text: str, kind: T, value: str | None = None) -> Positive:
    return (text, kind, value if value is not None else text)


POSITIVES: Final[list[Positive]] = [
    # PAN: any case, optional spaces/hyphens (those need "PAN" nearby), in sentences.
    _pos("ABCDE1234F", T.PAN),
    _pos("My PAN is ABCPD1234E.", T.PAN, "ABCPD1234E"),
    _pos("pan: abcpd1234e", T.PAN, "abcpd1234e"),
    _pos("PAN ABCDE 1234 F", T.PAN, "ABCDE 1234 F"),
    _pos("PAN no. ABCDE-1234-F", T.PAN, "ABCDE-1234-F"),
    _pos("(AAAPL1234C)", T.PAN, "AAAPL1234C"),
    _pos("AAAPL1234C is the holder's", T.PAN, "AAAPL1234C"),
    _pos("Please send it to BNZPM2501F", T.PAN, "BNZPM2501F"),
    _pos("PAN:ABCDE1234F,", T.PAN, "ABCDE1234F"),
    _pos("pan card AbCdE1234f", T.PAN, "AbCdE1234f"),
    _pos(f"PAN {to_fullwidth('ABCDE1234F')}", T.PAN, to_fullwidth("ABCDE1234F")),
    _pos("line one\nABCDE1234F\nline three", T.PAN, "ABCDE1234F"),
    # Aadhaar: Verhoeff-valid 12 digits (4-4-4 or contiguous), or masked with context.
    _pos(f"Aadhaar {group(A[0], (4, 4, 4))}", T.AADHAAR, group(A[0], (4, 4, 4))),
    _pos(
        f"My aadhaar no. is {group(A[1], (4, 4, 4), '-')}.", T.AADHAAR, group(A[1], (4, 4, 4), "-")
    ),
    _pos(A[2], T.AADHAAR),
    _pos(f"UID: {A[3]} verified", T.AADHAAR, A[3]),
    _pos(f"({group(A[4], (4, 4, 4))})", T.AADHAAR, group(A[4], (4, 4, 4))),
    _pos(f"ID {group(A[5], (4, 4, 4))}, issued 2019", T.AADHAAR, group(A[5], (4, 4, 4))),
    _pos(
        f"आधार {to_devanagari(group(A[6], (4, 4, 4)))}",
        T.AADHAAR,
        to_devanagari(group(A[6], (4, 4, 4))),
    ),
    _pos(f"aadhaar {to_fullwidth(A[7])}", T.AADHAAR, to_fullwidth(A[7])),
    _pos("Aadhaar XXXX XXXX 1234", T.AADHAAR, "XXXX XXXX 1234"),
    _pos("aadhaar no: xxxx-xxxx-5678", T.AADHAAR, "xxxx-xxxx-5678"),
    _pos("UIDAI ********9012", T.AADHAAR, "********9012"),
    _pos("Aadhaar: •••• •••• 3456", T.AADHAAR, "•" * 4 + " " + "•" * 4 + " 3456"),
    _pos("XXXX XXXX 7890 (aadhaar)", T.AADHAAR, "XXXX XXXX 7890"),
    # Bank account: 9-18 digits with a context word within 40 characters.
    _pos("account number 123456789", T.BANK_ACCOUNT, "123456789"),
    _pos("A/c No. 50100123456789", T.BANK_ACCOUNT, "50100123456789"),
    _pos("acct: 000123456789012345", T.BANK_ACCOUNT, "000123456789012345"),
    _pos("IFSC HDFC0000123, a/c 12345678901", T.BANK_ACCOUNT, "12345678901"),
    _pos("Bank account 987654321098", T.BANK_ACCOUNT, "987654321098"),
    _pos("savings account no 31234567890", T.BANK_ACCOUNT, "31234567890"),
    _pos("beneficiary 123456789012345", T.BANK_ACCOUNT, "123456789012345"),
    _pos("credited to a/c 0123456789 yesterday", T.BANK_ACCOUNT, "0123456789"),
    _pos("Account:112233445566", T.BANK_ACCOUNT, "112233445566"),
    _pos("acc no 4455667788 at SBI", T.BANK_ACCOUNT, "4455667788"),
    _pos("567890123456 is my account", T.BANK_ACCOUNT, "567890123456"),
    _pos("ac. no. 98765432101", T.BANK_ACCOUNT, "98765432101"),
    # Demat: NSDL "IN" + 14 digits; CDSL 16 digits with context.
    _pos("IN30123456789012", T.DEMAT_ID),
    _pos("DP ID: IN301234 56789012", T.DEMAT_ID, "IN301234 56789012"),
    _pos("in30123456789012 (nsdl)", T.DEMAT_ID, "in30123456789012"),
    _pos("NSDL IN30012310000001,", T.DEMAT_ID, "IN30012310000001"),
    _pos("CDSL BO ID 1201234567890123", T.DEMAT_ID, "1201234567890123"),
    _pos("demat 12012345 67890123", T.DEMAT_ID, "12012345 67890123"),
    _pos("client id 1301234500012345", T.DEMAT_ID, "1301234500012345"),
    _pos("BOID: 1208160012345678", T.DEMAT_ID, "1208160012345678"),
    _pos("My demat account is IN30302871234567.", T.DEMAT_ID, "IN30302871234567"),
    _pos("dp-id IN300214-12345678", T.DEMAT_ID, "IN300214-12345678"),
    _pos("1203320012345678 (CDSL)", T.DEMAT_ID, "1203320012345678"),
    # Card: 13-19 digits, Luhn-valid, any issuer, contiguous or consistently grouped.
    _pos(VISA, T.CARD),
    _pos(f"card {group(VISA, (4, 4, 4, 4))}", T.CARD, group(VISA, (4, 4, 4, 4))),
    _pos(group(MASTER, (4, 4, 4, 4), "-"), T.CARD),
    _pos(f"Amex {group(AMEX, (4, 6, 5))}", T.CARD, group(AMEX, (4, 6, 5))),
    _pos(f"Diners {group(DINERS, (4, 6, 4))}", T.CARD, group(DINERS, (4, 6, 4))),
    _pos(f"RuPay card: {RUPAY}.", T.CARD, RUPAY),
    _pos(f"{VISA13} expires 12/27", T.CARD, VISA13),
    _pos(f"card {CARD19}", T.CARD, CARD19),
    _pos(f"card {group(CARD19, (4, 4, 4, 4, 3))}", T.CARD, group(CARD19, (4, 4, 4, 4, 3))),
    _pos(f"(debit {group(RUPAY, (4, 4, 4, 4))})", T.CARD, group(RUPAY, (4, 4, 4, 4))),
    _pos(f"paid with {to_fullwidth(VISA)}", T.CARD, to_fullwidth(VISA)),
    # UPI: handle@psp for a configured PSP suffix.
    _pos("ravi@okaxis", T.UPI_ID),
    _pos("9876543210@ybl", T.UPI_ID),
    _pos("pay to a.b-c_d@okhdfcbank now", T.UPI_ID, "a.b-c_d@okhdfcbank"),
    _pos("UPI: shop123@paytm", T.UPI_ID, "shop123@paytm"),
    _pos("upi id ravi@oksbi.", T.UPI_ID, "ravi@oksbi"),
    _pos("RAVI@YBL", T.UPI_ID),
    _pos("name@ibl, thanks", T.UPI_ID, "name@ibl"),
    _pos("x1@axl is mine", T.UPI_ID, "x1@axl"),
    _pos("send 500 to priya@upi", T.UPI_ID, "priya@upi"),
    _pos("merchant@icici", T.UPI_ID),
    # Passport: letter + 7 digits with "passport" nearby.
    _pos("passport J8369854", T.PASSPORT, "J8369854"),
    _pos("Passport No: A1234567", T.PASSPORT, "A1234567"),
    _pos("passport: K0123456", T.PASSPORT, "K0123456"),
    _pos("My passport is M1234567.", T.PASSPORT, "M1234567"),
    _pos("passport m1234567", T.PASSPORT, "m1234567"),
    _pos("travel document P7654321", T.PASSPORT, "P7654321"),
    _pos("Passport # R1234560", T.PASSPORT, "R1234560"),
    _pos("N1234567 (passport)", T.PASSPORT, "N1234567"),
    _pos("passport number Z 1234567", T.PASSPORT, "Z 1234567"),
    _pos("Passport\nT7654321", T.PASSPORT, "T7654321"),
    # Voter ID (EPIC): 3 letters + 7 digits with context.
    _pos("voter id ABC1234567", T.VOTER_ID, "ABC1234567"),
    _pos("EPIC no. XYZ9876543", T.VOTER_ID, "XYZ9876543"),
    _pos("Voter ID: abc1234567", T.VOTER_ID, "abc1234567"),
    _pos("voter card ABC/1234567", T.VOTER_ID, "ABC/1234567"),
    _pos("EPIC ABC-1234567", T.VOTER_ID, "ABC-1234567"),
    _pos("elector photo identity card ABC1234567", T.VOTER_ID, "ABC1234567"),
    _pos("electoral roll entry DEF7654321", T.VOTER_ID, "DEF7654321"),
    _pos("ABC1234567 (voter ID)", T.VOTER_ID, "ABC1234567"),
    _pos("Voter-ID ABC 1234567", T.VOTER_ID, "ABC 1234567"),
    _pos("election card no GHJ1234567.", T.VOTER_ID, "GHJ1234567"),
]

NEGATIVES: Final[list[Negative]] = [
    # PAN
    ("GSTIN 27ABCDE1234F1Z5", T.PAN),
    ("ABCDE12345", T.PAN),
    ("ABCD1234F", T.PAN),
    ("ABCDE 1234 F", T.PAN),  # separated without context
    ("TABLE 2023 A summary", T.PAN),
    ("ABCDE1234FG", T.PAN),
    ("XABCDE1234F", T.PAN),
    # Aadhaar
    (f"Revenue {verhoeff_invalid('23456789012')} units", T.AADHAAR),
    ("XXXX XXXX 1234", T.AADHAAR),  # masked without context
    ("aadhaar card XXXX XXXX XXXX 1234", T.AADHAAR),  # a masked card
    (f"aadhaar {A[0][:11]}", T.AADHAAR),
    (f"aadhaar {verhoeff_complete('12345678901')}", T.AADHAAR),  # starts with 1
    (f"aadhaar {A[0]}5", T.AADHAAR),
    # Bank account
    ("invoice 123456789 dated", T.BANK_ACCOUNT),
    ("account 12345678", T.BANK_ACCOUNT),
    (f"account {luhn_invalid('123456789012345678')}", T.BANK_ACCOUNT),
    ("account balance ₹123456789", T.BANK_ACCOUNT),
    ("account" + " filler" * 6 + " 123456789", T.BANK_ACCOUNT),
    ("account 123456789.50", T.BANK_ACCOUNT),
    ("a/c HDFC123456789", T.BANK_ACCOUNT),
    # Demat
    ("IN3012345678901", T.DEMAT_ID),
    ("ISIN INE002A01018", T.DEMAT_ID),
    (f"{luhn_invalid('120123456789012')} units sold", T.DEMAT_ID),
    ("IN301234567890123", T.DEMAT_ID),
    ("DP ID 12345678", T.DEMAT_ID),
    # Card
    (luhn_invalid("411111111111111"), T.CARD),
    ("0000 0000 0000 0000", T.CARD),
    (VISA[:12], T.CARD),
    (f"{VISA[:4]} {VISA[4:8]}-{VISA[8:12]} {VISA[12:]}", T.CARD),
    (luhn_complete("4111111111111111111"), T.CARD),  # 20 digits
    ("card XXXX XXXX XXXX 1234", T.CARD),
    # UPI
    ("cfo@company.com", T.UPI_ID),
    ("ravi@paytm.com", T.UPI_ID),
    ("ravi@gmail", T.UPI_ID),
    ("@ybl", T.UPI_ID),
    ("ravi@yblx", T.UPI_ID),
    ("ravi@ybl.in", T.UPI_ID),
    # Passport
    ("J8369854", T.PASSPORT),
    ("passport fee 12345678", T.PASSPORT),
    ("passport AB1234567", T.PASSPORT),
    ("passport J836985", T.PASSPORT),
    ("passport J83698541", T.PASSPORT),
    # Voter ID
    ("ABC1234567", T.VOTER_ID),
    ("voter AB1234567", T.VOTER_ID),
    ("voter ABC123456", T.VOTER_ID),
    ("voter ABCD1234567", T.VOTER_ID),
    ("voter GHI12345678", T.VOTER_ID),
]

# AC #3: benign business content; nothing at all may be flagged.
BENIGN: Final[list[str]] = [
    "GSTIN 27ABCDE1234F1Z5",
    "GSTIN: 27AAPFU0939F1ZV, registered in Maharashtra",
    "27ABCDE1234F1Z5",
    "CIN L17110MH1973PLC019786",
    "U72200KA2010PTC052373 is the CIN",
    "LLPIN AAB-1234",
    "ISIN INE002A01018",
    "INF209K01YN0 (mutual fund ISIN)",
    "NSE: RELIANCE, BSE: 500325, TCS.NS",
    "HDFCBANK closed at 1,642.30",
    "Results for FY 2025-03-31 and 31/03/2025",
    "Deal value ₹1,23,45,678",
    "Rs 234567890124 deposited at the bank",
    "Revenue 4,215.5 cr, EBITDA 812 crore, PAT 312.4 mn",
    "Margin up 12.5% to 34 %",
    "Write to cfo@company.com or ir@reliance.co.in",
    "Call the bank on +91 22 6000 0000",
    "Tel: 022 6000 0000",
    "Mobile +919876543210, account manager",
    "IFSC HDFC0001234",
    "The deal team met on 2025-10-08 at 10:30.",
    f"Revenue {verhoeff_invalid('23456789012')} units",
    f"{luhn_invalid('411111111111111')} order reference",
]
