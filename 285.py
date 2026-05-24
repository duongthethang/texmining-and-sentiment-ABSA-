from playwright.sync_api import sync_playwright
import json
import os

# ====================== CẤU HÌNH ======================
STAR_CONFIGS = [
    (5, 0, "5star"),
    (4, 1, "4star"),
    (3, 2, "3star"),
    (2, 3, "2star"),
    (1, 4, "1star"),
]

CLEAR_ALL_SELECTOR = "button.bv-rnr__sc-16j1lpy-4"
REVIEW_TEXT_SELECTOR = 'div[id^="bv-review-text-"]'
MAX_LOAD_MORE = 200
MAX_STALL = 3
OUTPUT_DIR = "ysl_reviews"

# ── JS chạy 1 lần duy nhất trong browser để lấy toàn bộ id + title + text ──
# Thay vì Python gọi DOM N lần (1 lần/review) → gọi JS 1 lần duy nhất (~100x nhanh hơn)
JS_BATCH_COLLECT = """
() => {
    const results = [];
    const textEls = document.querySelectorAll('div[id^="bv-review-text-"]');
    textEls.forEach(el => {
        const rid   = el.id || "";
        const text  = (el.innerText || "").trim();
        const num   = rid.replace("bv-review-text-", "");
        const h3El  = document.querySelector("#bv-review-" + num + " h3");
        const title = h3El ? (h3El.innerText || "").trim() : "";
        if (rid && text) {
            results.push({ id: rid, title: title, text: text });
        }
    });
    return results;
}
"""


def extract_product_code(url: str) -> str:
    path = url.rstrip("/").split("?")[0]
    basename = path.split("/")[-1]
    return basename.replace(".html", "") or "unknown"


def auto_filename(product_code: str, star: int) -> str:
    return os.path.join(OUTPUT_DIR, f"{product_code}_{star}star.json")


def save_to_file(data: list, filepath: str):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    print(f"   💾 Đã lưu {len(data)} bình luận → {filepath}")


def scrape_all_star_ratings(product_url: str, max_load_more: int = MAX_LOAD_MORE):
    all_results = {}
    product_code = extract_product_code(product_url)
    print(f"🏷️  Mã sản phẩm: {product_code}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        print(f"🌐 Đang mở trang: {product_url}")
        page.goto(product_url, timeout=60000)
        page.wait_for_timeout(5000)

        # ====================== HÀM TIỆN ÍCH ======================
        def count_reviews() -> int:
            return len(page.locator(REVIEW_TEXT_SELECTOR).all())

        def deep_scroll_to_bottom(rounds: int = 3):
            for _ in range(rounds):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(900)
                page.mouse.wheel(0, 5000)
                page.wait_for_timeout(700)

        def scroll_to_review_section():
            try:
                section = page.locator('[id*="reviews"], [class*="reviews"], [class*="bv-"]').first
                section.scroll_into_view_if_needed(timeout=5000)
                page.wait_for_timeout(1500)
            except Exception:
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(1500)

        def click_clear_all() -> bool:
            try:
                clear_btn = page.locator(CLEAR_ALL_SELECTOR)
                if clear_btn.is_visible(timeout=4000):
                    clear_btn.scroll_into_view_if_needed()
                    clear_btn.click()
                    print("   🧹 Đã click Clear All → Reset filter")
                    page.wait_for_timeout(3000)
                    return True
                else:
                    print("   ⚠️ Không tìm thấy nút Clear All")
                    return False
            except Exception as e:
                print(f"   ⚠️ Lỗi click Clear All: {e}")
                return False

        def click_star_filter(star: int, nth: int) -> bool:
            print(f"⭐ Đang click filter {star} sao (nth={nth})...")
            strategies = [
                lambda: page.locator('div[role="button"]:has(div.bv-rnr__ppunu1-0)').nth(nth),
                lambda: page.locator(
                    f'div[role="button"]:nth-of-type({nth + 1}) div.bv-rnr__ppunu1-0'
                ).first,
                lambda: page.locator(
                    f'[aria-label*="{star} star"], [data-rating="{star}"]'
                ).first,
            ]
            for idx, get_loc in enumerate(strategies):
                try:
                    loc = get_loc()
                    if loc.is_visible(timeout=4000):
                        loc.scroll_into_view_if_needed()
                        loc.click()
                        print(f"   ✅ Click filter {star}⭐ thành công (strategy {idx+1})")
                        page.wait_for_timeout(5000)
                        return True
                except Exception as e:
                    print(f"   ↳ Strategy {idx+1} thất bại: {e}")
            print(f"   ❌ Không click được filter {star} sao")
            return False

        def load_all_reviews_for_current_filter(star: int) -> list:
            seen_ids: set = set()
            comments: list = []
            stall_count = 0

            deep_scroll_to_bottom()
            print(f"📜 Bắt đầu load review {star}⭐ (tối đa {max_load_more} lần)...")

            for i in range(max_load_more):
                try:
                    load_more = page.get_by_role("button", name="Load more").or_(
                        page.get_by_text("Load more", exact=True)
                    )
                    if not load_more.is_visible(timeout=3000):
                        print(f"\n   ✅ Hết nút 'Load more' → Đã load xong {star}⭐!")
                        break

                    before_count = count_reviews()
                    load_more.scroll_into_view_if_needed()
                    load_more.click()
                    print(f"   → Click Load more lần {i+1}", end="", flush=True)

                    no_change_cycles = 0
                    prev_count = before_count

                    for _ in range(15):
                        page.wait_for_timeout(1000)
                        deep_scroll_to_bottom(rounds=1)
                        page.wait_for_timeout(500)
                        current_count = count_reviews()
                        delta = current_count - prev_count
                        if delta > 0:
                            print(f" | +{delta}", end="", flush=True)
                            prev_count = current_count
                            no_change_cycles = 0
                        else:
                            no_change_cycles += 1
                            if no_change_cycles >= 3:
                                print(f" | ổn định ({current_count} review)", flush=True)
                                break

                    net_new = count_reviews() - before_count
                    if net_new == 0:
                        stall_count += 1
                        print(f"   ⚠️ Không có review mới (stall {stall_count}/{MAX_STALL})")
                        if stall_count >= MAX_STALL:
                            print("   🛑 Stall quá nhiều lần → Dừng!")
                            break
                    else:
                        stall_count = 0

                except Exception as e:
                    print(f"\n   ⚠️ Lỗi lần {i+1}: {e}")
                    stall_count += 1
                    if stall_count >= MAX_STALL:
                        break

            # ── Scroll cuối để đảm bảo render đủ ──
            print(f"\n   ⏳ Scroll cuối để render hết review {star}⭐...")
            deep_scroll_to_bottom(rounds=5)
            page.wait_for_timeout(4000)

            # ── BATCH COLLECT: 1 lần gọi JS duy nhất ──────────────────────────
            # Không lặp Python từng phần tử → nhanh hơn ~100x so với cũ
            print(f"   🔍 Đang thu thập bình luận {star}⭐ (batch JS)...")
            raw_data = page.evaluate(JS_BATCH_COLLECT)
            print(f"   ✅ Tổng phần tử DOM: {len(raw_data)}")

            for item in raw_data:
                rid = item.get("id", "")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    comments.append({
                        "id": rid,
                        "title": item.get("title", ""),
                        "text": item.get("text", ""),
                        "star": star,
                    })

            return comments

        # ====================== VÒNG LẶP CHÍNH: 5⭐ → 1⭐ ======================
        scroll_to_review_section()

        for star, nth, suffix in STAR_CONFIGS:
            print(f"\n{'='*60}")
            print(f"🌟 BẮT ĐẦU THU THẬP {star} SAO")
            print(f"{'='*60}")

            clicked = click_star_filter(star, nth)
            if not clicked:
                print(f"⚠️ Bỏ qua {star}⭐ do không click được filter")
                all_results[star] = []
                continue

            comments = load_all_reviews_for_current_filter(star)
            all_results[star] = comments

            filepath = auto_filename(product_code, star)
            save_to_file(comments, filepath)
            print(f"   📊 Tổng unique {star}⭐: {len(comments)} bình luận")

            if star > 1:
                scroll_to_review_section()
                click_clear_all()
                page.wait_for_timeout(2000)
                scroll_to_review_section()

        browser.close()

    return all_results


# ====================== CHẠY ======================
if __name__ == "__main__":
    url = "https://www.yslbeautyus.com/fragrance/mens-fragrances/y/y-eau-de-parfum/727YSL.html?dwvar_727YSL_size=3.3%20oz."
    results = scrape_all_star_ratings(url, max_load_more=MAX_LOAD_MORE)

    print(f"\n{'='*60}")
    print("🎉 HOÀN TẤT TOÀN BỘ!")
    print(f"{'='*60}")
    total = 0
    for star in [5, 4, 3, 2, 1]:
        count = len(results.get(star, []))
        total += count
        print(f"   {'⭐' * star} {star} sao: {count} bình luận")
    print(f"   📦 Tổng cộng: {total} bình luận")
    print(f"   📁 Files lưu tại thư mục: ./{OUTPUT_DIR}/")
