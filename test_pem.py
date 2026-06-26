import re


def fix_pem_formatting(pem_string: str) -> str:
    if not pem_string:
        return pem_string
    pem_string = pem_string.replace("\\n", "\n")
    if "\n" not in pem_string:
        match = re.search(
            r"(-----BEGIN [^-]+-----)(.*?)(-----END [^-]+-----)", pem_string
        )
        if match:
            header = match.group(1)
            raw_body = re.sub(r"\s+", "", match.group(2))
            body_lines = [raw_body[i : i + 64] for i in range(0, len(raw_body), 64)]
            body = "\n".join(body_lines)
            footer = match.group(3)
            return f"{header}\n{body}\n{footer}"
    return pem_string


pem = "-----BEGIN PRIVATE KEY-----MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDEdK-----END PRIVATE KEY-----"
print(repr(fix_pem_formatting(pem)))
