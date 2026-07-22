import time
import math
import pandas as pd
import streamlit as st
from supabase import create_client, Client
from streamlit_geolocation import streamlit_geolocation

st.set_page_config(page_title="Driver App", layout="centered")

st.markdown("""
    <style>
    .stButton>button { height: 60px; font-size: 18px; font-weight: bold; border-radius: 8px; }
    iframe[title*="streamlit_geolocation"] { transform: scale(1.3); transform-origin: top left; height: 65px !important; }
    </style>
""", unsafe_allow_html=True)

@st.cache_resource
def init_connection():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase: Client = init_connection()

def get_settings():
    req = supabase.table("settings").select("*").eq("id", 1).execute()
    if not req.data:
        default_settings = {"id": 1, "rate_per_kg": 2.0, "min_price": 10.0, "weight_threshold": 15.0}
        supabase.table("settings").insert(default_settings).execute()
        return default_settings
    return req.data[0]

sys_settings = get_settings()

def upsert_client(phone, name, city="", address="", coords=""):
    if not phone or not name: return
    existing = supabase.table("clients").select("*").eq("phone", phone).execute()
    payload = {"phone": phone, "full_name": name}
    if city: payload["city"] = city
    if address: payload["address"] = address
    if coords: payload["coordinates"] = coords
    
    if existing.data: supabase.table("clients").update(payload).eq("phone", phone).execute()
    else: supabase.table("clients").insert(payload).execute()

# --- ФУНКЦІЯ ДЛЯ РОЗРАХУНКУ ВІДСТАНІ ---
def calc_distance(lat1, lon1, coords_str):
    if not coords_str or "," not in coords_str: 
        return float('inf')
    try:
        lat2, lon2 = map(float, coords_str.split(','))
        R = 6371 # Радіус Землі в км
        dLat = math.radians(lat2 - lat1)
        dLon = math.radians(lon2 - lon1)
        a = math.sin(dLat/2) * math.sin(dLat/2) + math.cos(math.radians(lat1)) \
            * math.cos(math.radians(lat2)) * math.sin(dLon/2) * math.sin(dLon/2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        return R * c # Відстань у км
    except:
        return float('inf')

st.title("🚚 Панель Водія")

batches_req = supabase.table("batches").select("*").neq("status", "Завершено").order("departure_day").limit(2).execute()
if not batches_req.data:
    st.info("Наразі немає активних рейсів для виконання.")
    st.stop()

active_batches = [b['batch_id'] for b in batches_req.data]
my_batch = st.selectbox("Ваші поточні рейси:", active_batches)

# --- ГЛОБАЛЬНА ГЕОЛОКАЦІЯ ---
st.markdown("### 📍 Ваша локація")
location = streamlit_geolocation()
has_location = location and location.get('latitude') and location.get('longitude')
driver_lat, driver_lon = (location['latitude'], location['longitude']) if has_location else (None, None)

if has_location:
    st.success("✅ Локацію визначено! Маршрут автоматично відсортовано від найближчого.")
else:
    st.info("💡 Натисніть кнопку вище, щоб відсортувати посилки за відстанню та побудувати маршрут.")

if "active_camera" not in st.session_state: st.session_state.active_camera = None

tab1, tab2, tab3 = st.tabs(["📋 Мій маршрут", "➕ Прийняти посилку", "📦 Завантаження"])

# ==========================================
# Вкладка 1: МАРШРУТ ТА ФОТО-ДОКАЗ
# ==========================================
with tab1:
    shipments_req = supabase.table("shipments").select("*").eq("batch_id", my_batch).execute()
    df = pd.DataFrame(shipments_req.data)

    if not df.empty:
        # Виключаємо видані посилки з активного маршруту
        df_active = df[df['status'] != "Видано клієнту"].copy()
        
        # СОРТУВАННЯ ЗА ВІДСТАННЮ
        if has_location and not df_active.empty:
            df_active['distance'] = df_active['coordinates'].apply(lambda x: calc_distance(driver_lat, driver_lon, x))
            df_active = df_active.sort_values('distance')
            
            # КНОПКА GOOGLE MAPS ДЛЯ МУЛЬТИ-МАРШРУТУ (до 10 точок)
            valid_destinations = df_active[df_active['distance'] != float('inf')]['coordinates'].tolist()[:10]
            if valid_destinations:
                origin = f"{driver_lat},{driver_lon}"
                if len(valid_destinations) == 1:
                    maps_url = f"https://www.google.com/maps/dir/?api=1&origin={origin}&destination={valid_destinations[0]}"
                else:
                    dest = valid_destinations.pop() # Остання точка стає кінцевою
                    waypoints = "|".join(valid_destinations) # Решта - проміжні
                    maps_url = f"https://www.google.com/maps/dir/?api=1&origin={origin}&destination={dest}&waypoints={waypoints}"
                
                st.markdown(f'<a href="{maps_url}" target="_blank" style="display: block; text-align: center; background-color: #34A853; color: white; padding: 15px; border-radius: 8px; text-decoration: none; font-weight: bold; font-size: 18px; margin-bottom: 20px;">🗺️ Побудувати маршрут ({len(valid_destinations)+1} точок)</a>', unsafe_allow_html=True)
        
        search_query = st.text_input("🔍 Швидкий пошук (Введіть Трек або Місто)")
        if search_query: 
            df = df[df['tracking_id'].str.contains(search_query, case=False, na=False) | df['city_ua'].str.contains(search_query, case=False, na=False)]
            df_active = df[df['status'] != "Видано клієнту"]
            
        st.subheader(f"📦 Посилок для видачі: {len(df_active)} шт.")
        
        # Виводимо спочатку активні (відсортовані), потім завершені
        display_df = pd.concat([df_active, df[df['status'] == "Видано клієнту"]])
        
        for index, row in display_df.iterrows():
            dist_text = ""
            if has_location and row['status'] != "Видано клієнту":
                dist = calc_distance(driver_lat, driver_lon, row.get('coordinates', ''))
                if dist != float('inf'):
                    dist_text = f" [~{dist:.1f} км]"

            status_icon = "✅" if row['status'] == "Видано клієнту" else "📍"
            with st.expander(f"{status_icon} {row['city_ua']} — {row['recipient_ua']}{dist_text}"):
                st.write(f"**Трек:** {row['tracking_id']}")
                st.write(f"**Кількість місць:** {row.get('package_count', 1)} шт.")
                st.write(f"**Адреса:** {row['address']}")
                st.write(f"**Телефон:** {row.get('recipient_phone', 'Не вказано')}")
                st.write(f"**До сплати (Борг):** ₴{row['due_uah']}")
                
                if row.get('proof_url'):
                    st.image(row['proof_url'], caption="Доказ вручення", use_container_width=True)
                
                if row.get('coordinates'):
                    coords = row['coordinates']
                    single_map_url = coords if "http" in coords else f"https://www.google.com/maps/search/?api=1&query={coords}"
                    st.markdown(f'<a href="{single_map_url}" target="_blank" style="display: block; text-align: center; background-color: #4285F4; color: white; padding: 10px; border-radius: 5px; text-decoration: none; font-weight: bold; margin-bottom: 10px;"> навігатор до цієї точки</a>', unsafe_allow_html=True)
                
                if row['status'] != "Видано клієнту":
                    if st.session_state.active_camera != row['tracking_id']:
                        if st.button("📷 Сфотографувати видачу", key=f"btn_open_{row['tracking_id']}", width="stretch"):
                            st.session_state.active_camera = row['tracking_id']
                            st.rerun()
                    else:
                        photo = st.camera_input("📸 Сфотографувати", key=f"cam_{row['tracking_id']}")
                        if st.button("❌ Скасувати", key=f"btn_cancel_{row['tracking_id']}", width="stretch"):
                            st.session_state.active_camera = None
                            st.rerun()
                        
                        if photo:
                            file_name = f"{row['tracking_id'].replace('/', '_')}_{int(time.time())}.jpg"
                            supabase.storage.from_("proofs").upload(file_name, photo.getvalue(), {"content-type": "image/jpeg"})
                            photo_url = supabase.storage.from_("proofs").get_public_url(file_name)
                            
                            supabase.table("shipments").update({"status": "Видано клієнту", "proof_url": photo_url}).eq("tracking_id", row['tracking_id']).execute()
                            st.session_state.active_camera = None 
                            st.success("✅ Видано та зафіксовано на фото!")
                            st.rerun()
    else:
        st.info("У цьому рейсі поки немає посилок.")

# ==========================================
# Вкладка 2: ПРИЙОМ НОВОЇ ПОСИЛКИ
# ==========================================
with tab2:
    st.subheader("Оформлення від клієнта")
    
    clients_req = supabase.table("clients").select("*").execute()
    clients_list = clients_req.data if clients_req.data else []
    client_options = ["--- Ввести вручну ---"] + [f"{c['phone']} ({c['full_name']})" for c in clients_list]
    
    selected_sender = st.selectbox("Автозаповнення Відправника (UK)", client_options)
    s_data = next((c for c in clients_list if f"{c['phone']} ({c['full_name']})" == selected_sender), {})
    
    selected_recipient = st.selectbox("Автозаповнення Отримувача (UA)", client_options)
    r_data = next((c for c in clients_list if f"{c['phone']} ({c['full_name']})" == selected_recipient), {})

    if "my_coords" not in st.session_state: st.session_state.my_coords = ""
    if st.session_state.get("clear_my_coords"):
        st.session_state.my_coords = ""
        st.session_state.clear_my_coords = False

    # Беремо координати з глобальної кнопки (згори), якщо вони є
    default_coords = f"{driver_lat}, {driver_lon}" if has_location else st.session_state.my_coords
    final_coords = st.text_input("Координати (GPS)", value=default_coords, key="manual_coords")

    with st.form("driver_new_package_form", clear_on_submit=True):
        sender_name = st.text_input("ПІБ Відправника*", value=s_data.get("full_name", ""))
        sender_phone = st.text_input("Телефон Відправника*", value=s_data.get("phone", ""))
        recipient_name = st.text_input("ПІБ Отримувача*", value=r_data.get("full_name", ""))
        recipient_phone = st.text_input("Телефон Отримувача*", value=r_data.get("phone", ""))
        city_ua = st.text_input("Місто*", value=r_data.get("city", ""))
        address = st.text_input("Точна адреса*", value=r_data.get("address", ""))
        contents = st.text_input("Вміст (коротко)*")
        
        col3, col4, col5 = st.columns(3)
        weight = col3.number_input("Вага (кг)", min_value=0.1, value=1.0)
        pkg_count = col4.number_input("Кількість місць*", min_value=1, value=1)
        box_number = col5.text_input("№ Ящика (пусто=авто)")
        due_uah = st.number_input("Накладений платіж (Борг) ₴", min_value=0, value=0)

        if st.form_submit_button("✅ Зберегти та Прийняти", width="stretch"):
            if not sender_name or not recipient_name or not city_ua or not address:
                st.error("❌ Заповніть обов'язкові поля")
            else:
                if weight <= sys_settings["weight_threshold"]: calculated_price_gbp = sys_settings["min_price"]
                else: calculated_price_gbp = sys_settings["min_price"] + ((weight - sys_settings["weight_threshold"]) * sys_settings["rate_per_kg"])

                if not box_number:
                    existing_pkgs = supabase.table("shipments").select("id").eq("batch_id", my_batch).execute()
                    box_suffix = str(len(existing_pkgs.data) + 1 if existing_pkgs.data else 1)
                else: box_suffix = str(box_number).strip()

                new_tracking_id = f"{my_batch}/{box_suffix}" if my_batch != "Немає активних рейсів" else f"UKUA-{int(time.time())}/{box_suffix}"

                upsert_client(sender_phone, sender_name, coords=final_coords) 
                upsert_client(recipient_phone, recipient_name, city_ua, address)

                supabase.table("shipments").insert({
                    "tracking_id": new_tracking_id, "sender_uk": sender_name, "sender_phone": sender_phone,
                    "recipient_ua": recipient_name, "recipient_phone": recipient_phone, "city_ua": city_ua,
                    "address": address, "coordinates": final_coords, "contents": contents,
                    "package_count": pkg_count, "loaded_count": pkg_count, 
                    "weight_kg": weight, "batch_id": my_batch,
                    "status": "Прийнято водієм", "due_uah": due_uah, "price_gbp": calculated_price_gbp
                }).execute()
                
                st.success(f"Посилку прийнято! Трек: {new_tracking_id}")
                st.session_state.clear_my_coords = True
                time.sleep(1.5)
                st.rerun()

# ==========================================
# Вкладка 3: РОЗУМНЕ ЗАВАНТАЖЕННЯ БУСА
# ==========================================
with tab3:
    st.subheader("Скан-аут: Перевірка перед виїздом з UK")
    
    fresh_req = supabase.table("shipments").select("tracking_id, status, city_ua, package_count, loaded_count").eq("batch_id", my_batch).execute()
    fresh_data = fresh_req.data if fresh_req.data else []
    
    if not fresh_data:
        st.info("У цьому рейсі ще немає зареєстрованих посилок.")
    else:
        total_pieces = sum((p.get('package_count') or 1) for p in fresh_data)
        loaded_pieces = sum((p.get('loaded_count') or 0) for p in fresh_data)
        
        progress = loaded_pieces / total_pieces if total_pieces > 0 else 0
        st.progress(min(progress, 1.0))
        st.markdown(f"**Прогрес завантаження:** {loaded_pieces} з {total_pieces} фізичних місць в бусі")
        
        scanned_val = None
        with st.form("scan_load_form", clear_on_submit=True):
            manual_val = st.text_input("🔍 Введіть номер ящика (напр. '5') або повний трек")
            if st.form_submit_button("Завантажити (Enter)", width="stretch"):
                scanned_val = manual_val

        if scanned_val:
            clean_scan = str(scanned_val).strip()
            target_track = f"{my_batch}/{clean_scan}" if "/" not in clean_scan else clean_scan
            match = next((p for p in fresh_data if p['tracking_id'] == target_track), None)
            
            if not match:
                st.error(f"❌ Коробка '{target_track}' НЕ З ЦЬОГО РЕЙСУ або не існує!")
            else:
                current_loaded = match.get('loaded_count') or 0
                total_pkg_count = match.get('package_count') or 1
                
                if current_loaded >= total_pkg_count:
                    st.warning(f"⚠️ Всі {total_pkg_count} місць для посилки '{target_track}' ВЖЕ В БУСІ!")
                else:
                    new_loaded = current_loaded + 1
                    new_status = "Завантажено в бус" if new_loaded == total_pkg_count else f"Частково завантажено ({new_loaded} з {total_pkg_count})"
                    
                    supabase.table("shipments").update({"loaded_count": new_loaded, "status": new_status}).eq("tracking_id", target_track).execute()
                    if new_loaded == total_pkg_count:
                        st.success(f"✅ ПОСИЛКУ {target_track} ЗІБРАНО ПОВНІСТЮ!")
                    else:
                        st.info(f"📦 Знайдено {new_loaded}/{total_pkg_count} місць для {target_track}.")
                    time.sleep(1.5)
                    st.rerun()
                    
        st.divider()
        col_p, col_l = st.columns(2)
        with col_p:
            st.markdown("🔴 **ЩЕ ТРЕБА ЗНАЙТИ:**")
            pending = [p for p in fresh_data if (p.get('loaded_count') or 0) < (p.get('package_count') or 1)]
            for p in pending: st.write(f"- {p['tracking_id']} ({p.get('loaded_count') or 0}/{p.get('package_count') or 1})")
                
        with col_l:
            st.markdown("🟢 **В БУСІ:**")
            completed = [p for p in fresh_data if (p.get('loaded_count') or 0) >= (p.get('package_count') or 1)]
            for p in completed: st.write(f"- {p['tracking_id']}")