import requests
import os
import re
import json
import datetime
import time
import random
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import markdown
import google.generativeai as genai
from google.auth.transport.requests import Request

# --- Konfigurasi ---
API_BASE_URL = "https://ngocoks.com/wp-json/wp/v2/posts"
STATE_FILE = 'published_posts.json'
RANDOM_IMAGES_FILE = 'random_images.json'

# --- Konfigurasi Gemini API ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not set. Please set it in your GitHub Secrets or local environment.")
genai.configure(api_key=GEMINI_API_KEY)

# --- PERUBAHAN: Memisahkan model Gemini untuk konten dan judul ---
gemini_model_content = genai.GenerativeModel("gemini-1.5-flash") # Untuk editing konten
gemini_model_title = genai.GenerativeModel("gemini-1.5-flash")   # Untuk editing judul

# --- Konfigurasi Blogger (Menggunakan OAuth 2.0) ---
BLOGGER_BLOG_ID = os.getenv("BLOGGER_BLOG_ID")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")

# --- Penggantian Kata Khusus ---
REPLACEMENT_MAP = {
    "memek": "serambi lempit",
    "kontol": "rudal",
    "ngentot": "menggenjot",
    "vagina": "serambi lempit",
    "penis": "rudal",
    "seks": "bercinta",
    "mani": "kenikmatan",
    "sex": "bercinta"
}

# === Utilitas ===

def extract_first_image_url(html_content):
    """
    Mencari URL gambar pertama di dalam konten HTML.
    """
    match = re.search(r'<img[^>]+src="([^"]+)"', html_content, re.IGNORECASE)
    if match:
        return match.group(1)
    return None

def strip_html_and_divs(html):
    """
    Menghapus sebagian besar tag HTML, kecuali yang esensial,
    dan mengganti </p> dengan dua newline untuk pemisahan paragraf.
    """
    html_with_newlines = re.sub(r'</p>', r'\n\n', html, flags=re.IGNORECASE)
    html_no_images = re.sub(r'<img[^>]*>', '', html_with_newlines)
    html_no_divs = re.sub(r'</?div[^>]*>', '', html_no_images, flags=re.IGNORECASE)
    clean_text = re.sub('<[^<]+?>', '', html_no_divs)
    clean_text = re.sub(r'\n{3,}', r'\n\n', clean_text).strip()
    return clean_text

def remove_anchor_tags(html_content):
    """Menghapus tag <a> tapi mempertahankan teks di dalamnya."""
    return re.sub(r'<a[^>]*>(.*?)<\/a>', r'\1', html_content)

def sanitize_filename(title):
    """Membersihkan judul agar cocok untuk nama file."""
    clean_title = re.sub(r'[^\w\s-]', '', title).strip().lower()
    return re.sub(r'[-\s]+', '-', clean_title)

def replace_custom_words(text):
    """Menerapkan penggantian kata khusus pada teks."""
    processed_text = text
    sorted_replacements = sorted(REPLACEMENT_MAP.items(), key=lambda item: len(item[0]), reverse=True)
    for old_word, new_word in sorted_replacements:
        pattern = re.compile(re.escape(old_word), re.IGNORECASE)
        processed_text = pattern.sub(new_word, processed_text)
    return processed_text

# --- Fungsi pengeditan judul dengan Gemini ---
def edit_title_with_gemini(original_title, edited_first_300_words_context):
    print(f"🤖 Memulai pengeditan judul dengan Gemini AI (Model Judul): '{original_title}' berdasarkan 300 kata pertama yang diedit...")
    try:
        prompt = (
            f"Saya membutuhkan SATU judul baru yang sangat menarik (clickbait) dan nakal, namun tetap relevan"
            f"Paling penting, cari dan gunakan **peran atau pekerjaan tokoh wanita** yang mungkin disebutkan di awal cerita sebagai bagian dari judul untuk membuatnya lebih spesifik dan memancing rasa penasaran (misalnya: 'istri pejabat', 'guru', 'mahasiswi', 'dokter'). "
            f"Jika tidak ada peran atau pekerjaan yang jelas, buat judul yang fokus pada situasi atau hubungan."
            f"Judul harus deskriptif dan SEO Friendly\n\n"
            f"Berikut adalah 300 kata pertama dari artikel yang sudah diedit:\n\n"
            f"```\n{edited_first_300_words_context}\n```\n\n"
            f"**HANYA BERIKAN SATU JUDUL BARU, TANPA PENJELASAN ATAU TEKS TAMBAHAN APAPUN.**\n\n"
            f"Judul asli: '{original_title}'\n\n"
            f"Judul baru:"
        )
        response = gemini_model_title.generate_content(prompt)
        edited_title = response.text.strip()
        if edited_title.startswith('"') and edited_title.endswith('"'):
            edited_title = edited_title[1:-1]
            
        print(f"✅ Gemini AI (Model Judul) selesai mengedit judul. Hasil: '{edited_title}'")
        return edited_title
    except Exception as e:
        print(f"❌ Error saat mengedit judul dengan Gemini AI (Model Judul): {e}. Menggunakan judul asli.")
        return original_title

# --- Mengubah output dari edit_first_300_words_with_gemini ---
def edit_first_300_words_with_gemini(post_id, post_title, full_text_content):
    """
    Mengirim 300 kata pertama ke Gemini AI untuk diedit,
    dan menggabungkannya kembali dengan sisa artikel.
    Mengembalikan seluruh konten yang sudah diedit DAN 300 kata pertama yang diedit.
    """
    words = full_text_content.split()

    if len(words) < 50:
        print(f"[{post_id}] Artikel terlalu pendek (<50 kata) untuk diedit oleh Gemini AI. Melewati pengeditan.")
        cleaned_content = strip_html_and_divs(replace_custom_words(full_text_content))
        return cleaned_content, cleaned_content.split()[:300].join(" ") 
    
    char_count_for_300_words = 0
    word_count = 0
    
    for i, word in enumerate(words):
        if word_count < 300:
            char_count_for_300_words += len(word)
            if i < len(words) - 1:
                char_count_for_300_words += 1
            word_count += 1
        else:
            break
            
    char_count_for_300_words = min(char_count_for_300_words, len(full_text_content))

    first_300_words_original_string = full_text_content[:char_count_for_300_words].strip()
    rest_of_article_text = full_text_content[char_count_for_300_words:].strip()

    print(f"🤖 Memulai pengeditan Gemini AI (Model Konten) untuk artikel ID: {post_id} - '{post_title}' ({len(first_300_words_original_string.split())} kata pertama)...")

    try:
        prompt = (
            f"Cerita Berikut adalah cuplikan dari 300 kata pertama dari cerita utuhnya "
            f"Paraphrase signifikan setiap kata, sehingga 300 kata pertama ini beda dari aslinya, gunakan bahasa informal dan lugas :\n\n"
            f"{first_300_words_original_string}"
        )

        response = gemini_model_content.generate_content(prompt)
        edited_text_from_gemini = response.text

        print(f"✅ Gemini AI (Model Konten) selesai mengedit bagian pertama artikel ID: {post_id}.")

        cleaned_edited_first_300_words = strip_html_and_divs(edited_text_from_gemini)

        final_combined_text = cleaned_edited_first_300_words.strip() + "\n\n" + rest_of_article_text.strip()
        
        return strip_html_and_divs(final_combined_text), cleaned_edited_first_300_words
    except Exception as e:
        print(f"❌ Error saat mengedit dengan Gemini AI (Model Konten) untuk artikel ID: {post_id} - {e}. Menggunakan teks asli untuk bagian ini.")
        content_after_replacements = replace_custom_words(full_text_content)
        cleaned_content = strip_html_and_divs(content_after_replacements)
        return cleaned_content, cleaned_content.split()[:300].join(" ")


# --- Fungsi untuk memuat dan menyimpan status postingan yang sudah diterbitkan ---
def load_published_posts_state():
    """Memuat ID postingan yang sudah diterbitkan dari file state."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            try:
                return set(json.load(f))
            except json.JSONDecodeError:
                print(f"Warning: {STATE_FILE} is corrupted or empty. Starting with an empty published posts list.")
                return set()
    return set()

def save_published_posts_state(published_ids):
    """Menyimpan ID postingan yang sudah diterbitkan ke file state."""
    with open(STATE_FILE, 'w') as f:
        json.dump(list(published_ids), f)

def load_image_urls(file_path):
    """
    Memuat daftar URL gambar dari file JSON.
    """
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            try:
                urls = json.load(f)
                if isinstance(urls, list) and all(isinstance(url, str) for url in urls):
                    print(f"✅ Berhasil memuat {len(urls)} URL gambar dari '{file_path}'.")
                    return urls
                else:
                    print(f"❌ Error: Konten '{file_path}' bukan daftar string URL yang valid.")
                    return []
            except json.JSONDecodeError:
                print(f"❌ Error: Gagal mengurai JSON dari '{file_path}'. Pastikan formatnya benar.")
                return []
    else:
        print(f"⚠️ Peringatan: File '{file_path}' tidak ditemukan. Tidak ada gambar acak yang akan ditambahkan.")
        return []

def get_random_image_url(image_urls):
    """
    Memilih URL gambar secara acak dari daftar.
    """
    if image_urls:
        return random.choice(image_urls)
    return None

# === Inisialisasi Layanan Blogger dengan OAuth 2.0 ===
def get_blogger_service_with_oauth():
    """
    Menginisialisasi dan mengembalikan objek layanan Blogger API menggunakan OAuth 2.0
    dengan refresh token.
    """
    if not all([GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN]):
        raise ValueError(
            "GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, dan GOOGLE_REFRESH_TOKEN "
            "variabel lingkungan harus disetel untuk otentikasi OAuth."
        )

    token_uri = 'https://oauth2.googleapis.com/token'
    scopes = ['https://www.googleapis.com/auth/blogger']

    creds = Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri=token_uri,
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=scopes
    )

    try:
        creds.refresh(Request())
    except Exception as e:
        print(f"❌ Gagal me-refresh token akses: {e}")
        print("Pastikan GOOGLE_REFRESH_TOKEN Anda valid dan belum dicabut.")
        raise

    service = build('blogger', 'v3', credentials=creds)
    return service

# === Fungsi untuk Mengirim Post ke Blogger ===
def publish_post_to_blogger(blogger_service, blog_id, title, content_markdown, tags=None, random_image_url=None):
    """
    Menerbitkan postingan ke Blogger dari konten Markdown,
    dengan opsi menambahkan gambar acak di awal, dan parameter <!--more--> setelah paragraf pertama.
    """
    print(f"🚀 Menerbitkan '{title}' ke Blogger...")

    # Konversi Markdown ke HTML
    content_html = markdown.markdown(content_markdown)

    # Pisahkan konten menjadi paragraf
    paragraphs = content_html.split('\n\n')

    final_content_for_blogger = ""

    # Tambahkan gambar acak di awal konten jika ada
    if random_image_url:
        image_html = f'<p style="text-align: center;"><img src="{random_image_url}" alt="{title}" style="max-width: 100%; height: auto; display: block; margin: 0 auto; border-radius: 8px;"></p>'
        final_content_for_blogger += image_html + "\n"
        print(f"🖼️ Gambar acak '{random_image_url}' ditambahkan ke artikel.")

    # Tambahkan paragraf pertama dan parameter <!--more-->
    if paragraphs:
        final_content_for_blogger += paragraphs[0] + "\n\n<!--more-->\n\n"
        print("💡 Parameter '<!--more-->' ditambahkan setelah paragraf pertama.")
        # Tambahkan sisa paragraf setelah paragraf pertama
        final_content_for_blogger += '\n\n'.join(paragraphs[1:])
    else:
        print("⚠️ Artikel tidak memiliki paragraf. Tidak ada parameter '<!--more-->' yang ditambahkan.")
        final_content_for_blogger += content_html # Jika tidak ada paragraf, tambahkan saja konten aslinya

    # Buat payload postingan
    post_body = {
        'kind': 'blogger#post',
        'blog': {'id': blog_id},
        'title': title,
        'content': final_content_for_blogger,
        'labels': tags if tags else []
    }

    try:
        request = blogger_service.posts().insert(blogId=blog_id, body=post_body)
        response = request.execute()
        print(f"✅ Artikel '{title}' berhasil diterbitkan ke Blogger! URL: {response.get('url')}")
        return response
    except Exception as e:
        print(f"❌ Gagal menerbitkan artikel '{title}' ke Blogger: {e}")
        print(f"Error details: {e.args}")
        return None

# === Ambil semua postingan dari WordPress Self-Hosted REST API ===
def fetch_all_and_process_posts():
    """
    Mengambil semua postingan dari WordPress self-hosted REST API, membersihkan HTML,
    dan menerapkan penggantian kata khusus.
    """
    all_posts_raw = []
    page = 1
    per_page_limit = 100

    print("📥 Mengambil semua artikel dari WordPress self-hosted REST API...")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    while True:
        params = {
            'per_page': per_page_limit,
            'page': page,
            'status': 'publish',
            '_fields': 'id,title,content,excerpt,categories,tags,date,featured_media'
        }
        try:
            res = requests.get(API_BASE_URL, params=params, headers=headers, timeout=30)
            
            if res.status_code == 400:
                if "rest_post_invalid_page_number" in res.text:
                    print(f"Reached end of posts from WordPress API (page {page} does not exist). Stopping fetch.")
                    break
                else:
                    raise Exception(f"Error: Gagal mengambil data dari WordPress REST API: {res.status_code} - {res.text}. "
                                     f"Pastikan URL API Anda benar dan dapat diakses.")
            elif res.status_code != 200:
                raise Exception(f"Error: Gagal mengambil data dari WordPress REST API: {res.status_code} - {res.text}. "
                                 f"Pastikan URL API Anda benar dan dapat diakses.")

            posts_batch = res.json()

            if not posts_batch:
                print(f"Fetched empty batch on page {page}. Stopping fetch.")
                break

            all_posts_raw.extend(posts_batch)
            page += 1
            time.sleep(0.5)

        except requests.exceptions.Timeout:
            print(f"Timeout: Permintaan ke WordPress API di halaman {page} habis waktu. Mungkin ada masalah jaringan atau server lambat.")
            break
        except requests.exceptions.RequestException as e:
            print(f"Network Error: Gagal terhubung ke WordPress API di halaman {page}: {e}. Cek koneksi atau URL.")
            break

    processed_posts = []
    for post in all_posts_raw:
        original_title = post.get('title', {}).get('rendered', '')
        post['original_title'] = original_title

        raw_content = post.get('content', {}).get('rendered', '')
        content_image_url = extract_first_image_url(raw_content)
        post['content_image_url'] = content_image_url

        content_no_anchors = remove_anchor_tags(raw_content)
        cleaned_formatted_content = strip_html_and_divs(content_no_anchors)
        content_after_replacements = replace_custom_words(cleaned_formatted_content)

        post['raw_cleaned_content'] = content_after_replacements
        post['id'] = post.get('id')
        processed_posts.append(post)

    return processed_posts

# === Eksekusi Utama ===
if __name__ == '__main__':
    print(f"[{datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] Starting WordPress to Blogger publishing process...")
    print("🚀 Mengambil semua artikel WordPress self-hosted.")
    print("🤖 Fitur Pengeditan 300 Kata Pertama oleh Gemini AI DIAKTIFKAN.")
    print("🤖 Fitur Pengeditan Judul oleh Gemini AI DIAKTIFKAN.")
    print("💡 Parameter '<!--more-->' akan ditambahkan setelah paragraf pertama.")
    print("🖼️ Mencoba mengambil gambar pertama dari konten artikel.")
    print("Directly publishing to Blogger, no local Markdown file generation.")

    try:
        # 1. Muat daftar postingan yang sudah diterbitkan
        published_ids = load_published_posts_state()
        print(f"Ditemukan {len(published_ids)} postingan yang sudah diterbitkan sebelumnya.")

        # 2. Muat URL gambar acak
        random_image_urls = load_image_urls(RANDOM_IMAGES_FILE)
        selected_random_image = get_random_image_url(random_image_urls)
        if not selected_random_image:
            print("⚠️ Tidak ada URL gambar acak yang tersedia. Artikel akan diterbitkan tanpa gambar acak.")

        # 3. Ambil semua postingan dari API WordPress dan lakukan pre-processing
        all_posts_preprocessed = fetch_all_and_process_posts()
        print(f"Total {len(all_posts_preprocessed)} artikel ditemukan dan diproses awal dari WordPress API.")

        # 4. Filter postingan yang belum diterbitkan
        unpublished_posts = [post for post in all_posts_preprocessed if str(post['id']) not in published_ids]
        print(f"Ditemukan {len(unpublished_posts)} artikel yang belum diterbitkan.")

        if not unpublished_posts:
            print("\n🎉 Tidak ada artikel baru yang tersedia untuk diterbitkan hari ini. Proses selesai.")
            exit()

        # 5. Urutkan postingan yang belum diterbitkan dari yang TERBARU
        unpublished_posts.sort(key=lambda x: datetime.datetime.fromisoformat(x['date'].replace('Z', '+00:00')), reverse=True)

        # 6. Pilih satu postingan untuk diterbitkan hari ini
        post_to_publish = unpublished_posts[0]

        print(f"🌟 Memproses dan menerbitkan artikel berikutnya: '{post_to_publish.get('original_title')}' (ID: {post_to_publish.get('id')})")

        # LAKUKAN PENGEDITAN AI UNTUK KONTEN DAN JUDUL
        final_processed_content, edited_first_300_words_context = edit_first_300_words_with_gemini(
            post_to_publish['id'],
            post_to_publish['original_title'],
            post_to_publish['raw_cleaned_content']
        )
        
        final_edited_title = edit_title_with_gemini(
            replace_custom_words(post_to_publish['original_title']),
            edited_first_300_words_context
        )

        # 7. Inisialisasi layanan Blogger menggunakan OAuth 2.0
        blogger_service = get_blogger_service_with_oauth()

        # 8. Terbitkan ke Blogger dengan judul yang sudah diedit
        if blogger_service and BLOGGER_BLOG_ID:
            publish_post_to_blogger(
                blogger_service,
                BLOGGER_BLOG_ID,
                final_edited_title,
                final_processed_content,
                random_image_url=selected_random_image
            )
        else:
            print("Skipping Blogger publishing: BLOGGER_BLOG_ID atau layanan Blogger tidak dikonfigurasi/diinisialisasi.")

        # 9. Tambahkan ID postingan ke daftar yang sudah diterbitkan dan simpan state
        published_ids.add(str(post_to_publish['id']))
        save_published_posts_state(published_ids)
        print(f"✅ State file '{STATE_FILE}' diperbarui.")

        print("\n🎉 Proses Selesai! Artikel telah diterbitkan langsung ke Blogger.")

    except Exception as e:
        print(f"❌ Terjadi kesalahan fatal: {e}")
        import traceback
        traceback.print_exc()
