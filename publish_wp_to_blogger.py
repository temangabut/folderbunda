import os
import requests
import json
import datetime
import time
import random
import re
import markdown
import google.generativeai as genai
import unicodedata
from urllib.parse import quote_plus

# --- Hapus atau nonaktifkan impor WordPress XML-RPC
# from wordpress_xmlrpc import Client, WordPressPost
# from wordpress_xmlrpc.methods.posts import NewPost
# from wordpress_xmlrpc.methods.media import UploadFile

# --- Impor untuk Google API (yang dibutuhkan untuk Blogger) ---
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError # Tambahkan ini

# --- Variabel Rahasia yang Sudah Kamu Siapkan ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
BLOGGER_BLOG_ID = os.getenv("BLOGGER_BLOG_ID")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")

# Validasi variabel penting
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not set.")
if not BLOGGER_BLOG_ID:
    raise ValueError("BLOGGER_BLOG_ID environment variable not set.")
if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET or not GOOGLE_REFRESH_TOKEN:
    raise ValueError("GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, or GOOGLE_REFRESH_TOKEN environment variables not set. Please ensure all are configured.")

# --- Konfigurasi Gemini ---
genai.configure(api_key=GEMINI_API_KEY)
gemini_model_content = genai.GenerativeModel("gemini-1.5-flash")
gemini_model_title = genai.GenerativeModel("gemini-2.5-flash")

# --- Variabel dan Konstanta Lainnya (tetap sama) ---
API_SOURCE_URL = "https://ngocoks.com/wp-json/wp/v2/posts"
STATE_FILE = 'published_posts.json'
RANDOM_IMAGES_FILE = 'random_images.json'
DEFAULT_TAGS = ["Cerita Dewasa", "Cerita Seks", "Cerita Sex", "Cerita Ngentot"]

REPLACEMENT_MAP = {
    "memek": "serambi lempit",
    "kontol": "rudal",
    "ngentot": "menyetubuhi",
    "vagina": "serambi lempit",
    "penis": "rudal",
}

# --- Fungsi-fungsi Pembantu (tetap sama) ---
def extract_first_image_url(html_content):
    match = re.search(r'<img[^>]+src="([^"]+)"', html_content, re.IGNORECASE)
    if match:
        return match.group(1)
    return None

def strip_html_and_divs(html):
    if html is None:
        html = ""
    processed_text = re.sub(r'</p>', r'\n\n', html, flags=re.IGNORECASE)
    processed_text = re.sub(r'<img[^>]*>', '', processed_text)
    processed_text = re.sub(r'</?div[^>]*>', '', processed_text, flags=re.IGNORECASE)
    processed_text = re.sub('<[^<]+?>', '', processed_text)
    processed_text = re.sub(r'\n{3,}', r'\n\n', processed_text).strip()
    return processed_text

def remove_anchor_tags(html_content):
    return re.sub(r'<a[^>]*>(.*?)<\/a>', r'\1', html_content)

def sanitize_filename(title):
    clean_title = re.sub(r'[^\w\s-]', '', title).strip().lower()
    return re.sub(r'[-\s]+', '-', clean_title)

def slugify(value):
    value = str(value)
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('utf-8')
    value = re.sub(r'[^\w\s-]', '', value).strip().lower()
    return re.sub(r'[-\s]+', '-', value)

def replace_custom_words(text):
    processed_text = text
    sorted_replacements = sorted(REPLACEMENT_MAP.items(), key=lambda item: len(item[0]), reverse=True)
    for old_word, new_word in sorted_replacements:
        pattern = re.compile(re.escape(old_word), re.IGNORECASE)
        processed_text = pattern.sub(new_word, processed_text)
    return processed_text

def edit_title_with_gemini(original_title, edited_first_300_words_context):
    print(f"🤖 Memulai pengeditan judul dengan Gemini AI (Model Judul): '{original_title}' berdasarkan 300 kata pertama yang diedit...")
    try:
        prompt = (
            f"Saya membutuhkan SATU judul baru yang sangat menarik, contoh 'Bersetubuh Dengan Pelayan Toko Cantik di Penginapan'. "
            f"Paling penting, cari dan gunakan **peran atau pekerjaan tokoh wanita** yang mungkin disebutkan di awal cerita sebagai bagian dari judul untuk membuatnya lebih spesifik dan memancing rasa penasaran (misalnya: 'istri pejabat', 'guru', 'mahasiswi', 'dokter'). "
            f"Jika tidak ada peran atau pekerjaan yang jelas, buat judul yang fokus pada situasi atau hubungan. "
            f"Judul harus detail dan jelas.\n\n"
            f"Berikut adalah 300 kata pertama dari artikel yang sudah diedit:\n\n"
            f"```\n{edited_first_300_words_context}\n```\n\n"
            f"**HANYA BERIKAN SATU JUDUL BARU, TANPA PENJELASAN ATAK TEKS TAMBAHAN APAPUN.**\n\n"
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

def edit_first_300_words_with_gemini(post_id, post_title, full_text_content):
    words = full_text_content.split()
    
    if len(words) < 50:
        print(f"[{post_id}] Artikel terlalu pendek (<50 kata) untuk diedit oleh Gemini AI. Melewati pengeditan.")
        cleaned_content = strip_html_and_divs(replace_custom_words(full_text_content))
        return cleaned_content, cleaned_content

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
        processed_first_300_words = replace_custom_words(first_300_words_original_string)

        prompt = (
            f"Saya ingin Anda menulis ulang paragraf pembuka dari sebuah cerita. "
            f"Tujuannya adalah untuk membuat narasi yang mengalir, menarik perhatian pembaca, dan mempertahankan inti cerita asli, "
            f"tetapi dengan gaya bahasa yang lebih halus dan sopan, menghindari bahasa yang eksplisit atau vulgar. "
            f"Paragraf harus tetap panjangnya sekitar 300 kata dari teks asli, tetapi dengan kosakata dan struktur kalimat yang diubah secara signifikan. "
            f"Gunakan gaya informal dan lugas. Pastikan tidak ada konten yang melanggar pedoman keamanan.\n\n"
            f"Berikut adalah paragraf aslinya:\n\n"
            f"{processed_first_300_words}"
            f"\n\nParagraf yang ditulis ulang:"
        )
        response = gemini_model_content.generate_content(prompt)
        edited_text_from_gemini = response.text
        print(f"✅ Gemini AI (Model Konten) selesai mengedit bagian pertama artikel ID: {post_id}.")
        
        cleaned_edited_text_from_gemini = strip_html_and_divs(edited_text_from_gemini)
        
        final_combined_text = cleaned_edited_text_from_gemini.strip() + "\n\n" + replace_custom_words(rest_of_article_text).strip()
        
        fully_cleaned_content = strip_html_and_divs(final_combined_text)
        
        return fully_cleaned_content, cleaned_edited_text_from_gemini

    except Exception as e:
        print(f"❌ Error saat mengedit dengan Gemini AI (Model Konten) untuk artikel ID: {post_id} - {e}. Menggunakan teks asli untuk bagian ini.")
        content_after_replacements = replace_custom_words(full_text_content)
        cleaned_content = strip_html_and_divs(content_after_replacements)
        return cleaned_content, content_after_replacements[:char_count_for_300_words]

def load_published_posts_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            try:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
                else:
                    print(f"Warning: {STATE_FILE} content is not a list. Starting with an empty published posts list.")
                    return set()
            except json.JSONDecodeError:
                print(f"Warning: {STATE_FILE} is corrupted or empty. Starting with an empty published posts list.")
                return set()
    return set()

def save_published_posts_state(published_ids):
    with open(STATE_FILE, 'w') as f:
        json.dump(list(published_ids), f)

def load_image_urls(file_path):
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
    if image_urls:
        return random.choice(image_urls)
    return None

def insert_details_tag(content_text, article_url=None, article_title=None):
    paragraphs = content_text.split('\n\n')
    total_paragraphs = len(paragraphs)
    
    if total_paragraphs < 2:
        return content_text

    paragraph_insert_index = total_paragraphs // 2
    
    first_part = "\n\n".join(paragraphs[:paragraph_insert_index])
    rest_part = "\n\n".join(paragraphs[paragraph_insert_index:])

    encoded_article_url = ""
    encoded_article_title_for_display = ""
    if article_url and article_title:
        clean_article_url = article_url.rstrip('/')
        encoded_article_url = quote_plus(clean_article_url)
        encoded_article_title_for_display = article_title.replace('"', '&quot;')

    details_tag_start = f'<details><strong>Cerita Dewasa Ngentot - {encoded_article_title_for_display}</strong></details>\n'
    details_tag_end = '\n'
    
    print(f"📝 Tag <details> akan disisipkan setelah paragraf ke-{paragraph_insert_index} (total {total_paragraphs} paragraf).")
    return first_part + '\n\n' + details_tag_start + rest_part + details_tag_end

def add_more_tag_before_send(content_text):
    paragraphs = content_text.split('\n\n')
    
    if not paragraphs or not paragraphs[0].strip():
        print("⚠️ Konten terlalu pendek atau paragraf pertama kosong. Tidak menyisipkan <!--more-->.")
        return content_text

    first_paragraph = paragraphs[0].strip()
    rest_of_content = "\n\n".join(paragraphs[1:]).strip()
    
    # Sisipkan placeholder <!--more-->
    content_with_more_tag = first_paragraph + '\n\n<!--more-->\n\n' + rest_of_content
    print("📝 Placeholder <!--more--> disisipkan setelah paragraf pertama.")
    return content_with_more_tag

def fetch_raw_posts():
    all_posts_data = []
    page = 1
    per_page_limit = 100
    print(f"📥 Mengambil semua artikel mentah dari WordPress SUMBER: {API_SOURCE_URL}...")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    while True:
        params = {
            'per_page': per_page_limit,
            'page': page,
            'status': 'publish',
            '_fields': 'id,title,content,date'
        }
        try:
            res = requests.get(API_SOURCE_URL, params=params, headers=headers, timeout=30)
            
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
            for post in posts_batch:
                all_posts_data.append({
                    'id': post.get('id'),
                    'title': post.get('title', {}).get('rendered', ''),
                    'content': post.get('content', {}).get('rendered', ''),
                    'date': post.get('date', '')
                })
            page += 1
            time.sleep(0.5)
        except requests.exceptions.Timeout:
            print(f"Timeout: Permintaan ke WordPress API di halaman {page} habis waktu. Mungkin ada masalah jaringan atau server lambat.")
            break
        except requests.exceptions.RequestException as e:
            print(f"Network Error: Gagal terhubung ke WordPress API di halaman {page}: {e}. Cek koneksi atau URL.")
            break
    return all_posts_data


### Fungsi Baru untuk Publikasi ke Blogger


def get_blogger_credentials():
    # Bangun kredensial langsung dari refresh token
    creds = Credentials(
        token=None,  # Token akses akan didapatkan saat refresh
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri='https://oauth2.googleapis.com/token',
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=['https://www.googleapis.com/auth/blogger'] # Scope Blogger API
    )
    
    print("⚙️ Membangun kredensial dari environment variables (refresh token)...")
    try:
        # Paksa refresh untuk mendapatkan access token baru
        creds.refresh(Request())
        print("✅ Kredensial Blogger berhasil dibuat dan di-refresh dari ENV.")
        return creds
    except RefreshError as e: # Tangkap error spesifik untuk refresh token
        print(f"❌ Gagal me-refresh kredensial Blogger. Refresh token mungkin kedaluwarsa atau tidak valid: {e}")
        raise RuntimeError("Autentikasi Blogger gagal. Pastikan GOOGLE_REFRESH_TOKEN, CLIENT_ID, CLIENT_SECRET valid dan refresh token belum kedaluwarsa.")
    except Exception as e:
        print(f"❌ Gagal membangun atau me-refresh kredensial Blogger dari ENV: {e}")
        raise RuntimeError("Autentikasi Blogger gagal. Pastikan GOOGLE_REFRESH_TOKEN, CLIENT_ID, CLIENT_SECRET valid.")

def publish_post_to_blogger(creds, blog_id, title, content_html, random_image_url=None, post_status='LIVE', labels=None):
    print(f"🚀 Menerbitkan '{title}' ke Blogger Blog ID: {blog_id}...")
    
    try:
        service = build('blogger', 'v3', credentials=creds)
    except Exception as e:
        print(f"❌ Gagal inisialisasi layanan Blogger: {e}")
        return None

    final_content_for_blogger = content_html
    if random_image_url:
        image_html = f'<p><img src="{random_image_url}" alt="{title}" style="max-width: 100%; height: auto; display: block; margin: 0 auto;"></p>'
        final_content_for_blogger = image_html + "\n\n" + content_html
        print(f"🖼️ Gambar acak '{random_image_url}' ditambahkan ke artikel.")

    post_body = {
        'kind': 'blogger#post',
        'blog': {'id': blog_id},
        'title': title,
        'content': final_content_for_blogger,
        'labels': labels if labels else DEFAULT_TAGS, # Blogger menggunakan 'labels'
    }

    print("\n--- Debugging Detail Payload Blogger ---")
    print(f"Blog ID: {blog_id}")
    print(f"Title: {post_body['title']}")
    print(f"Labels: {post_body['labels']}")
    content_preview = post_body['content'][:500] + '...' if len(post_body['content']) > 500 else post_body['content']
    print(f"Content Preview (sebagian): {content_preview}")
    print("---------------------------------------\n")

    try:
        # isDraft True jika status bukan 'LIVE' (yaitu, 'DRAFT')
        request = service.posts().insert(blogId=blog_id, body=post_body, isDraft=(post_status != 'LIVE'))
        response = request.execute()
        
        post_id = response.get('id')
        post_url = response.get('url') # URL artikel yang sudah diterbitkan

        print(f"✅ Artikel '{title}' berhasil diterbitkan ke Blogger! Post ID: {post_id}, URL: {post_url}")
        return {'id': post_id, 'URL': post_url}

    except HttpError as e:
        print(f"❌ Terjadi kesalahan HTTP saat memposting ke Blogger: {e}")
        try:
            error_details = json.loads(e.content).get('error', {})
            print(f"Blogger API Error Code: {error_details.get('code')}")
            print(f"Blogger API Error Message: {error_details.get('message')}")
            if 'errors' in error_details:
                for err in error_details['errors']:
                    print(f"  Reason: {err.get('reason')}, Domain: {err.get('domain')}, Extended Message: {err.get('extendedHelp')}")
        except json.JSONDecodeError:
            print("Tidak dapat mengurai detail error HTTP.")
        return None
    except Exception as e:
        print(f"❌ Terjadi kesalahan umum saat memposting ke Blogger: {e}")
        return None


### Penyesuaian `if __name__ == '__main__':`

# Terakhir, kita ubah panggilan fungsi di bagian utama script:

if __name__ == '__main__':
    print(f"[{datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] Starting WordPress to BLOGGER publishing process...")
    print(f"🚀 Mengambil artikel dari WordPress SUMBER: {API_SOURCE_URL}.")
    print(f"🎯 Akan memposting ke BLOGGER dengan Blog ID: {BLOGGER_BLOG_ID}.")
    print("🤖 Fitur Pengeditan Judul dan Konten (300 kata pertama) oleh Gemini AI DIAKTIFKAN.")
    print("📝 Tag <details> akan disisipkan di dalam artikel di pertengahan total paragraf.")
    print("📝 Placeholder <!--more--> akan disisipkan setelah paragraf pertama, dan ANDA perlu menggantinya secara manual menjadi tag HTML asli jika diperlukan.")
    print("🖼️ Mencoba menambahkan gambar acak di awal konten.")
    print(f"🏷️ Label default yang akan ditambahkan (sebagai pengganti tags): {', '.join(DEFAULT_TAGS)}")

    try:
        published_ids = load_published_posts_state()
        print(f"Ditemukan {len(published_ids)} postingan yang sudah diterbitkan sebelumnya.")

        random_image_urls = load_image_urls(RANDOM_IMAGES_FILE)
        selected_random_image = get_random_image_url(random_image_urls)
        if not selected_random_image:
            print("⚠️ Tidak ada URL gambar acak yang tersedia. Artikel akan diterbitkan tanpa gambar acak di awal.")

        all_posts_raw_data = fetch_raw_posts()
        print(f"Total {len(all_posts_raw_data)} artikel ditemukan dari WordPress SUMBER.")

        unpublished_posts = [post for post in all_posts_raw_data if str(post['id']) not in published_ids]
        print(f"Ditemukan {len(unpublished_posts)} artikel dari SUMBER yang belum diterbitkan ke TARGET.")

        if not unpublished_posts:
            print("\n🎉 Tidak ada artikel baru yang tersedia untuk diterbitkan hari ini. Proses selesai.")
            exit()

        unpublished_posts.sort(key=lambda x: datetime.datetime.fromisoformat(x['date'].replace('Z', '+00:00')), reverse=True)

        post_to_publish_data = unpublished_posts[0]

        original_id = post_to_publish_data['id']
        original_title = post_to_publish_data['title']
        original_content = post_to_publish_data['content']
        post_datetime_obj = datetime.datetime.fromisoformat(post_to_publish_data['date'].replace('Z', '+00:00')) # Definisi di sini
        
        # Untuk Blogger, kita dapat memprediksi slug, tetapi URL lengkapnya baru didapat setelah publish
        post_slug = slugify(original_title) # Gunakan slug dari judul asli atau yang diedit nanti
        
        print(f"🌟 Memproses dan menerbitkan artikel berikutnya: '{original_title}' (ID: {original_id}) dari SUMBER")

        content_no_anchors = remove_anchor_tags(original_content)
        cleaned_content_before_gemini = strip_html_and_divs(content_no_anchors)
        content_after_replacements_all = replace_custom_words(cleaned_content_before_gemini)

        final_processed_content_text, edited_first_300_words_content = edit_first_300_words_with_gemini(
            original_id,
            original_title,
            content_after_replacements_all
        )

        final_edited_title = edit_title_with_gemini(
            replace_custom_words(original_title),
            edited_first_300_words_content
        )
        # Perbarui slug berdasarkan judul yang diedit
        post_slug = slugify(final_edited_title)

        # Untuk tag <details>, kita berikan URL placeholder atau dummy yang nanti perlu diganti manual
        # Karena URL Blogger asli baru didapat setelah post diterbitkan
        predicted_article_url_placeholder = f"https://your-blogger-blog.blogspot.com/{post_datetime_obj.strftime('%Y/%m')}/{post_slug}.html"
        print(f"🔗 URL artikel target (placeholder): {predicted_article_url_placeholder}")

        content_with_details_tag = insert_details_tag(
            final_processed_content_text,
            article_url=predicted_article_url_placeholder,
            article_title=final_edited_title
        )
        
        final_content_with_more_tag_placeholder = add_more_tag_before_send(content_with_details_tag)

        # Konversi newline ganda menjadi tag <p> dan pastikan format HTML benar
        final_post_content_html = final_content_with_more_tag_placeholder.replace('\n\n', '</p><p>')
        if not final_post_content_html.startswith('<p>'):
            final_post_content_html = '<p>' + final_post_content_html
        if not final_post_content_html.endswith('</p>'):
            final_post_content_html = final_post_content_html + '</p>'

        print("💡 Pratinjau Konten HTML Final (untuk cek <!--more--> placeholder):")
        print(final_post_content_html[:500] + '...' if len(final_post_content_html) > 500 else final_post_content_html)
        print("-------------------------------------------------------")

        # --- Panggil fungsi publikasi ke Blogger ---
        blogger_creds = get_blogger_credentials() # Dapatkan kredensial Blogger
        published_result = publish_post_to_blogger(
            blogger_creds,
            BLOGGER_BLOG_ID,
            final_edited_title,
            final_post_content_html,
            random_image_url=selected_random_image,
            labels=DEFAULT_TAGS # Gunakan 'labels' untuk Blogger
        )
        
        if published_result:
            published_ids.add(str(original_id))
            save_published_posts_state(published_ids)
            print(f"✅ State file '{STATE_FILE}' diperbarui dengan ID post dari SUMBER: {original_id}.")
            print("\n🎉 Proses Selesai! Artikel telah diterbitkan ke BLOGGER TARGET.")
            # Di sini kamu bisa mendapatkan URL asli dari published_result['URL']
            print(f"URL Artikel Blogger: {published_result['URL']}")
        else:
            print("\n❌ Gagal menerbitkan artikel ke BLOGGER TARGET. State file tidak diperbarui.")

    except Exception as e:
        print(f"❌ Terjadi kesalahan fatal: {e}")
        import traceback
        traceback.print_exc()
