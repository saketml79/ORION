"""Credential-value redaction shared by canonical and presentation producers.

Names, counters and endpoint metadata are not credentials. This is defense in
depth for recognizable credential fields/material, not secret discovery.
"""
import re


_NAME = (r"password|passwd|pwd|token|secret|credentials?|authorization|"
         r"private[_ .-]?key|api[_ .-]?key|access[_ .-]?key|client[_ .-]?(?:key|secret)|"
         r"(?:access|refresh|auth|authentication|bearer|api)[_ .-]?token")
_REFERENCES = (r".*(?:[_ .-](?:name|scope|ref|reference|id|count|counts)|"
               r"[_ .-]secret[_ .-]key|[_ .-]key[_ .-](?:name|id))")
CREDENTIAL_KEY = re.compile(r"^(?!" + _REFERENCES + r"$)(?:.*[_ .-])?(?:" + _NAME + r")$", re.I)
_LITERAL = r'''\\"(?:(?!\\")[\s\S])*\\"|\\'(?:(?!\\')[\s\S])*\\'|"(?:\\.|""|[^"\\])*"|'(?:\\.|''|[^'\\])*' '''.rstrip()
_VALUE = r"\[REDACTED\]|(?:" + _LITERAL + r")(?:\s*\+\s*(?:" + _LITERAL + r"))*|[^\s,;\)\]}&\"']+"
_LOOKUP = r"(?:\$\$?[A-Za-z_]|dbutils\.secrets\.get\s*\(|(?:os\.)?(?:getenv|environ)\b|System\.getenv\s*\()"
SECRET_TEXT = re.compile(
    r"(?P<material>\b(?:dapi[a-z0-9]{20,}|gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|"
    r"lsv2_[A-Za-z0-9_]+|AKIA[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b|"
    r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----[\s\S]*?"
    r"-----END (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----)|"
    r"(?P<bearer>\bBearer\s+)(?P<bearer_value>[^\s\"',;]+)|"
    r"(?P<url>(?<![a-z0-9+.-])[a-z][a-z0-9+.-]*://[^\s/@:]+:)(?P<url_value>[^\s/@]+)(?=@)|"
    r"(?P<assignment>(?<![\w])(?:[A-Za-z0-9]+[_ .-])?(?:" + _NAME + r")[\"']?\s*[:=]\s*)"
    r"(?P<assignment_value>(?!" + _LOOKUP + r")(?:(?:Basic|Bearer)\s+)?(?:" + _VALUE + r"))|"
    r"(?P<flag>--(?:password|passwd|pwd|token|secret|api[_-]?key|access[_-]?token|refresh[_-]?token)\s+)"
    r"(?P<flag_value>(?!" + _LOOKUP + r")(?:" + _VALUE + r"))|"
    r"(?P<identified>\bIDENTIFIED\s+BY\s+)(?P<identified_value>" + _VALUE + r")|"
    r"(?P<setter>\bset(?:Password|Token|Secret|ApiKey|AccessToken|RefreshToken|PrivateKey)\s*\(\s*)"
    r"(?P<setter_value>(?!" + _LOOKUP + r")(?:" + _VALUE + r"))|"
    r"(?P<put>\b(?:put|setProperty)\s*\(\s*[\"'](?:" + _NAME + r")[\"']\s*,\s*)"
    r"(?P<put_value>(?!" + _LOOKUP + r")(?:" + _VALUE + r"))", re.I
)


def is_secret_key(key):
    # Camel-case credentials are classified without matching tokenization etc.
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    return bool(CREDENTIAL_KEY.fullmatch(name))


def redact_text(value):
    def replace(match):
        if match.group("material"):
            return "[REDACTED]" + "\n" * match.group().count("\n")
        for group in ("bearer", "url", "assignment", "flag", "identified", "setter", "put"):
            prefix = match.group(group)
            if prefix is not None:
                raw = match.group(group + "_value")
                quote = raw[:2] if raw[:2] in {'\\"', "\\'"} else raw[0] if raw[:1] in {'"', "'"} else ""
                return prefix + quote + "[REDACTED]" + "\n" * raw.count("\n") + quote
        return "[REDACTED]"
    return SECRET_TEXT.sub(replace, value)