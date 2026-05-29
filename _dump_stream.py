import fitz
import sys
import pdf_core

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = "SafeApr.pdf"
ED = "_test_edited.pdf"

# Helper: does content-stream replace still find a given number on page 2?
def stream_replace_works(path, number):
    doc = fitz.open(path)
    page = doc[1]
    n = pdf_core._content_stream_replace(doc, page, number, "ZZZZ", limit=1)
    doc.close()
    return n

# Numbers on consecutive transactions (page 2 / index 1)
nums = [
    "242071756443919",  # 04/01
    "242071751561978",  # 04/02
    "242071752154084",  # 04/03
]

# Fresh original: all should work
print("=== on ORIGINAL ===")
for x in nums:
    print(f"  {x}: stream-replace count = {stream_replace_works(SRC, x)}")

# Simulate web flow: edit #1 via replace_block (original -> edited)
blocks = pdf_core.get_page_blocks(SRC, 2)
def block_with(num):
    for b in blocks:
        if num in b.text:
            return b
    return None

b1 = block_with(nums[0])
pdf_core.replace_block(SRC, ED, 2, b1.bbox, b1.text, b1.text.replace(nums[0], "dassadas"))
print(f"\n=== after edit #1 saved to {ED} (via _save_fitz garbage=4) ===")
for x in nums[1:]:
    print(f"  {x}: stream-replace count = {stream_replace_works(ED, x)}")

# Now do edit #2 on the EDITED file the way the web does (src==edited path region)
blocks2 = pdf_core.get_page_blocks(ED, 2)
b2 = next(b for b in blocks2 if nums[1] in b.text)
ok = pdf_core.replace_block(ED, ED, 2, b2.bbox, b2.text, b2.text.replace(nums[1], "ZZZZ"))
doc = fitz.open(ED)
txt = doc[1].get_text("text")
doc.close()
print(f"\n=== after edit #2 on the edited file ===")
print(f"  replace_block returned: {ok}")
print(f"  'ZZZZ' present: {'ZZZZ' in txt}")
print(f"  'dassadas' (edit1) survived: {'dassadas' in txt}")
print(f"  'S4O5C6O9S7P7' (neighbour of edit1) survived: {'S4O5C6O9S7P7' in txt}")
print(f"  'E7K6I6N4T4T0' (neighbour of edit2) survived: {'E7K6I6N4T4T0' in txt}")
print(f"  count 'Orig CO Name:Porter Freight F': {txt.count('Orig CO Name:Porter Freight F')}")
