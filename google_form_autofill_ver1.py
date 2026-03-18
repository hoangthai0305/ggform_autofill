"""
Google Form Auto-Fill Tool - TDTU Edition
==========================================
Cài đặt:
    pip install selenium webdriver-manager

Chạy:
    python google_form_autofill.py
"""

import time
import re
import unicodedata
from difflib import SequenceMatcher
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager


# ==========================================
# CẤU HÌNH
# ==========================================
TDTU_EMAIL      = ""
TDTU_PASSWORD   = ""
SOURCE_FORM_URL = ""
TARGET_FORM_URL = ""

SIMILARITY_THRESHOLD = 0.75
# ==========================================

PERSONAL_KEYWORDS = [
    "mssv", "mã số sinh viên", "họ tên", "họ và tên", "full name",
    "tên sinh viên", "tên học viên", "khoa", "faculty", "ngành", "lớp",
    "class", "email", "mail", "số điện thoại", "phone", "địa chỉ",
    "address", "ngày sinh", "birthday", "giới tính", "gender",
    "msv", "student id", "sinh viên", "học viên",
]

# JS tìm nút Next — thử tất cả các cách Google Form render nút này
CLICK_NEXT_JS = """
var keywords = ['tiếp', 'next', 'tiếp theo', 'continue'];

// Cách 1: tìm button có text khớp
var buttons = document.querySelectorAll('button, [role="button"]');
for (var i = 0; i < buttons.length; i++) {
    var txt = buttons[i].innerText.toLowerCase().trim();
    for (var k = 0; k < keywords.length; k++) {
        if (txt.includes(keywords[k])) {
            buttons[i].click();
            return 'clicked:' + buttons[i].innerText.trim();
        }
    }
}

// Cách 2: tìm span có text khớp rồi click span đó
var spans = document.querySelectorAll('span');
for (var i = 0; i < spans.length; i++) {
    var txt = spans[i].innerText.toLowerCase().trim();
    for (var k = 0; k < keywords.length; k++) {
        if (txt === keywords[k]) {
            spans[i].click();
            return 'span_clicked:' + spans[i].innerText.trim();
        }
    }
}

// Cách 3: tìm div.freebirdFormviewerViewNavigationNextButton
var nextDiv = document.querySelector(
    '.freebirdFormviewerViewNavigationNextButton, ' +
    '[data-action="next"], ' +
    '.appsMaterialWizButtonPaperbuttonLabel'
);
if (nextDiv) {
    nextDiv.click();
    return 'div_clicked';
}

return null;
"""

# JS kiểm tra trang hiện tại có nút Submit không
HAS_SUBMIT_JS = """
var keywords = ['gửi', 'submit', 'nộp'];
var buttons = document.querySelectorAll('button, [role="button"]');
for (var i = 0; i < buttons.length; i++) {
    var txt = buttons[i].innerText.toLowerCase().trim();
    for (var k = 0; k < keywords.length; k++) {
        if (txt.includes(keywords[k])) return true;
    }
}
return false;
"""


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────

def normalize(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r'^(cau|cau hoi|question|q)[\s\d\.\:\-]*', '', text)
    text = re.sub(r'^\d+[\.\)\-\s]+', '', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def is_personal_field(title: str) -> bool:
    norm = normalize(title)
    return any(kw in norm for kw in PERSONAL_KEYWORDS)


def best_match(target_title: str, source_questions: list):
    best_q, best_score = None, 0.0
    for q in source_questions:
        if q.get("is_personal"):
            continue
        score = similarity(target_title, q["title"])
        if score > best_score:
            best_score = score
            best_q = q
    if best_score >= SIMILARITY_THRESHOLD:
        return best_q, best_score
    return None, best_score


# ─────────────────────────────────────────
# DRIVER & LOGIN
# ─────────────────────────────────────────

def create_driver():
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


def google_login(driver, email, password):
    print("\n🔐 Đang đăng nhập...")
    wait = WebDriverWait(driver, 20)
    driver.get("https://accounts.google.com/signin")
    time.sleep(2)
    try:
        f = wait.until(EC.presence_of_element_located((By.ID, "identifierId")))
        f.clear(); f.send_keys(email)
        driver.find_element(By.ID, "identifierNext").click()
        time.sleep(2)
        p = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='password']")))
        p.clear(); p.send_keys(password)
        driver.find_element(By.ID, "passwordNext").click()
        time.sleep(4)
        if "challenge" in driver.current_url:
            print("⚠️  Xác minh 2 bước - hoàn thành rồi nhấn Enter...")
            input()
        print("✅ Đăng nhập thành công!")
        return True
    except TimeoutException:
        print("❌ Timeout.")
        return False


# ─────────────────────────────────────────
# SCRAPE FORM NGUỒN
# ─────────────────────────────────────────

SCRAPE_JS = """
var result = [];
var blocks = document.querySelectorAll("[role='listitem']");
blocks.forEach(function(block) {
    var titleEl = block.querySelector("[role='heading'], .M7eMe, .HoXoMd");
    if (!titleEl) return;
    var title = titleEl.innerText.trim();
    if (!title) return;

    var radios     = block.querySelectorAll("[role='radio']");
    var checkboxes = block.querySelectorAll("[role='checkbox']");
    var elems      = radios.length > 0 ? radios : checkboxes;
    if (elems.length === 0) return;

    var qType   = radios.length > 0 ? 'radio' : 'checkbox';
    var opts    = [];
    var correct = [];

    elems.forEach(function(el) {
        var label = (el.getAttribute('aria-label') || el.getAttribute('data-value') || '').trim();
        if (!label) return;
        opts.push(label);
        if (el.getAttribute('aria-checked') === 'true') {
            correct.push(label);
        }
    });

    if (opts.length > 0) {
        result.push({ title: title, type: qType, options: opts, correct: correct });
    }
});
return result;
"""


def click_next_button(driver) -> bool:
    """
    Thử click nút Next bằng JS.
    Trả về True nếu click thành công và trang đã thay đổi.
    """
    url_before = driver.current_url
    page_before = driver.execute_script("return document.body.innerHTML.length")

    result = driver.execute_script(CLICK_NEXT_JS)

    if result is None:
        return False

    # Chờ trang load
    time.sleep(2)

    # Kiểm tra nội dung trang có thay đổi không
    page_after = driver.execute_script("return document.body.innerHTML.length")
    url_after  = driver.current_url

    changed = (page_after != page_before) or (url_after != url_before)
    print(f"   ➡️  Click Next: {result} | Trang thay đổi: {changed}")
    return changed


def scrape_source(driver, url):
    print(f"\n📥 Scrape form nguồn...")
    driver.get(url)
    time.sleep(3)

    all_questions = []
    page_num = 1

    while True:
        print(f"   📄 Trang {page_num}...")
        time.sleep(1)

        page_data = driver.execute_script(SCRAPE_JS)
        for q in page_data:
            all_questions.append({
                "title":           q["title"],
                "type":            q["type"],
                "options":         q["options"],
                "correct_answers": q["correct"],
                "is_personal":     is_personal_field(q["title"]),
            })

        answered = sum(1 for q in page_data if q["correct"])
        print(f"      → {len(page_data)} câu, {answered} có đáp án tick")

        if not click_next_button(driver):
            break
        page_num += 1

    total = sum(1 for q in all_questions if q["correct_answers"] and not q["is_personal"])
    print(f"\n✅ Tổng {len(all_questions)} câu | Có đáp án: {total} câu")
    return all_questions


def display_scraped(questions):
    print("\n" + "="*65)
    print("📋 ĐÁP ÁN TỪ FORM NGUỒN")
    print("="*65)
    answered = 0
    for i, q in enumerate(questions, 1):
        if q["is_personal"]:
            continue
        print(f"\n  Câu {i}: {q['title'][:65]}")
        for opt in q["options"]:
            mark = "✅" if opt in q["correct_answers"] else "○ "
            print(f"           {mark}  {opt}")
        if not q["correct_answers"]:
            print(f"           ❓ [Không có đáp án]")
        else:
            answered += 1
    print(f"\n{'='*65}")
    print(f"📊 Câu có đáp án: {answered}")
    print(f"{'='*65}")
    return answered


# ─────────────────────────────────────────
# ĐIỀN 1 TRANG
# ─────────────────────────────────────────

def fill_current_page(driver, source_questions):
    """Điền tất cả câu trắc nghiệm trên trang hiện tại. Bỏ qua cá nhân."""
    filled = no_match = skipped = personal = 0

    page_items = driver.execute_script("""
        var result = [];
        var blocks = document.querySelectorAll("[role='listitem']");
        blocks.forEach(function(block, idx) {
            var titleEl = block.querySelector("[role='heading'], .M7eMe, .HoXoMd");
            if (!titleEl) return;
            var title = titleEl.innerText.trim();
            if (!title) return;
            var hasRadio    = block.querySelectorAll("[role='radio']").length > 0;
            var hasCheckbox = block.querySelectorAll("[role='checkbox']").length > 0;
            var hasText     = block.querySelectorAll("input[type='text'], textarea").length > 0;
            var qtype = hasRadio ? 'radio'
                      : hasCheckbox ? 'checkbox'
                      : hasText ? 'text' : 'unknown';
            result.push({ idx: idx, title: title, qtype: qtype });
        });
        return result;
    """)

    all_blocks = driver.find_elements(By.CSS_SELECTOR, "[role='listitem']")

    for item in page_items:
        q_title   = item["title"]
        block_idx = item["idx"]
        qtype     = item["qtype"]

        # ── Bỏ qua cá nhân ──
        if is_personal_field(q_title):
            personal += 1
            print(f"   👤 BỎ QUA   : {q_title[:55]}")
            continue

        # ── Fuzzy match ──
        matched, score = best_match(q_title, source_questions)

        if matched is None:
            best_debug = max(
                (q for q in source_questions if not q.get("is_personal")),
                key=lambda q: similarity(q_title, q["title"]),
                default=None
            )
            if best_debug:
                ds = similarity(q_title, best_debug["title"])
                print(f"   ❌ KHÔNG KHỚP: {q_title[:35]!r} | gần nhất: {best_debug['title'][:30]!r} ({ds:.0%})")
            else:
                print(f"   ❌ KHÔNG KHỚP: {q_title[:52]!r}")
            no_match += 1
            continue

        if not matched["correct_answers"]:
            print(f"   ⚠️  KHỚP ({score:.0%}) không có đáp án: {q_title[:42]}")
            skipped += 1
            continue

        try:
            block_elem = all_blocks[block_idx]
        except IndexError:
            print(f"   ⚠️  Không tìm thấy block: {q_title[:45]}")
            skipped += 1
            continue

        ans_list = matched["correct_answers"]
        success  = False

        if qtype == "radio":
            success = driver.execute_script("""
                var block  = arguments[0];
                var answer = arguments[1].toLowerCase().trim();
                var items  = block.querySelectorAll("[role='radio']");
                for (var i = 0; i < items.length; i++) {
                    var label = (
                        items[i].getAttribute('aria-label') ||
                        items[i].getAttribute('data-value') || ''
                    ).toLowerCase().trim();
                    if (!label) continue;
                    if (label === answer || label.includes(answer) || answer.includes(label)) {
                        items[i].click();
                        return true;
                    }
                }
                return false;
            """, block_elem, ans_list[0])

        elif qtype == "checkbox":
            any_ok = False
            for answer in ans_list:
                ok = driver.execute_script("""
                    var block  = arguments[0];
                    var answer = arguments[1].toLowerCase().trim();
                    var items  = block.querySelectorAll("[role='checkbox']");
                    for (var i = 0; i < items.length; i++) {
                        var label = (
                            items[i].getAttribute('aria-label') ||
                            items[i].getAttribute('data-value') || ''
                        ).toLowerCase().trim();
                        if (!label) continue;
                        if (label === answer || label.includes(answer) || answer.includes(label)) {
                            if (items[i].getAttribute('aria-checked') !== 'true') {
                                items[i].click();
                            }
                            return true;
                        }
                    }
                    return false;
                """, block_elem, answer)
                if ok:
                    any_ok = True
            success = any_ok

        elif qtype == "text":
            try:
                inp = block_elem.find_element(By.CSS_SELECTOR, "input[type='text'], textarea")
                inp.clear()
                inp.send_keys(ans_list[0])
                success = True
            except Exception:
                pass

        if success:
            filled += 1
            print(f"   ✅ ĐIỀN ({score:.0%}): {q_title[:38]!r}  →  {', '.join(ans_list)}")
        else:
            skipped += 1
            print(f"   ⚠️  THẤT BẠI ({score:.0%}): {q_title[:50]}")

    return filled, no_match, skipped, personal


# ─────────────────────────────────────────
# ĐIỀN TOÀN BỘ FORM (nhiều trang)
# ─────────────────────────────────────────

def fill_form(driver, target_url, source_questions):
    print(f"\n🤖 Mở form của bạn...")
    driver.get(target_url)
    time.sleep(3)

    total_filled = total_no_match = total_skipped = total_personal = 0
    page_num = 1

    while True:
        print(f"\n📄 Trang {page_num}")
        print("─" * 65)

        # Chờ form load xong
        time.sleep(1.5)

        # Điền trang hiện tại
        filled, no_match, skipped, personal = fill_current_page(driver, source_questions)
        total_filled   += filled
        total_no_match += no_match
        total_skipped  += skipped
        total_personal += personal

        print(f"\n   📊 Trang {page_num}: điền {filled} câu, bỏ qua {personal} cá nhân")

        # Thử chuyển sang trang tiếp theo
        has_next = click_next_button(driver)

        if has_next:
            page_num += 1
            # Chờ trang mới load hoàn toàn
            time.sleep(2)
            continue

        # Không có nút Next → đã đến trang cuối
        print(f"\n   ✅ Đã đến trang cuối (trang {page_num})")
        break

    # Tổng kết
    print(f"\n{'='*65}")
    print(f"🏁 HOÀN THÀNH!")
    print(f"   ✅ Đã điền tự động : {total_filled} câu trắc nghiệm")
    print(f"   👤 Bỏ qua cá nhân  : {total_personal} trường")
    print(f"   ❌ Không khớp      : {total_no_match} câu")
    if total_skipped:
        print(f"   ⚠️  Lỗi / thiếu   : {total_skipped} câu")
    print(f"{'='*65}")
    print(f"\n👉 Hãy điền MSSV, Họ tên, Khoa... rồi nhấn Gửi.")
    print(f"   Trình duyệt vẫn đang mở.")


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

def main():
    print("="*65)
    print("🎓  GOOGLE FORM AUTO-FILL  —  TDTU EDITION")
    print("="*65)

    email    = TDTU_EMAIL      or input("\n📧 Email trường: ").strip()
    password = TDTU_PASSWORD   or input("🔑 Mật khẩu: ").strip()
    src      = SOURCE_FORM_URL or input("\n🔗 Link form NGUỒN (đã tick đáp án): ").strip()
    tgt      = TARGET_FORM_URL or input("🔗 Link form CỦA BẠN: ").strip()

    driver = create_driver()
    try:
        if not google_login(driver, email, password):
            return

        source_questions = scrape_source(driver, src)
        if not source_questions:
            print("❌ Không đọc được câu hỏi.")
            return

        answered = display_scraped(source_questions)
        if answered == 0:
            print("\n⚠️  Không có câu nào có đáp án.")
            return

        input(f"\n✅ Nhấn Enter để mở form của bạn và bắt đầu điền {answered} câu...")
        fill_form(driver, tgt, source_questions)

        input("\nNhấn Enter để đóng trình duyệt khi đã gửi xong...")

    except Exception as e:
        print(f"\n❌ Lỗi: {e}")
        import traceback; traceback.print_exc()
        input("\nNhấn Enter để đóng...")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()