import time
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

# 🔥 ДОДАНО: Отримання тарифів із бази даних для водія
def get_settings():
    req = supabase.table("settings").select("*").eq("id", 1).execute()
    if not req.data:
        default_settings = {"id": 1, "rate_per_kg": 2.0, "min_price": 10.0, "weight_threshold": 15.0}
        supabase.table("settings").insert(default_settings).execute()
        return default_settings
    return req.data[0]

sys_settings = get_settings()

st.title("🚚 Панель Водія")

batches_req = supabase.table("batches").select("*").neq("status", "Завершено").order("departure_day").limit(2).execute()
if not batches_req.data:
    st.info("Наразі немає активних рейсів для виконання.")
    st.stop()

active_batches = [b['batch_id'] for b in batches_req.data]
my_batch = st.selectbox("Ваші поточні рейси:", active_batches)

# Ініціалізація змінної для ізоляції камери
if "active_camera" not in st.session_state:
    st.session_state.active_camera = None

tab1, tab2 = st.tabs(["📋 Мій маршрут", "➕ Прийняти посилку"])

# ==========================================
# Вкладка 1: МАРШРУТ, СКАНЕР ТА ФОТО-ДОКАЗ
# ==========================================
with tab1:
    shipments_req = supabase.table("shipments").select("*").eq("batch_id", my_batch).execute()
    df = pd.DataFrame(shipments_req.data)

    if not df.empty:
        search_query = st.text_input("🔍 Сканер (Введіть або відскануйте Tracking ID)", placeholder="UKUA-...")
        
        if search_query:
            df = df[df['tracking_id'].str.contains(search_query, case=False, na=False)]
            
        st.subheader(f"📦 Посилок у списку: {len(df)} шт.")
        
        for index, row in df.iterrows():
            with st.expander(f"📍 {row['city_ua']} — {row['recipient_ua']} ({row['status']})"):
                st.write(f"**Трек:** {row['tracking_id']}")
                st.write(f"**Адреса:** {row['address']}")
                st.write(f"**Телефон:** {row.get('recipient_phone', 'Не вказано')}")
                # Показуємо вартість доставки, щоб водій знав, скільки брати грошей
                st.write(f"**Ціна доставки (GBP):** £{row.get('price_gbp', 0)}")
                st.write(f"**До сплати (Борг):** ₴{row['due_uah']}")
                
                if row.get('proof_url'):
                    st.image(row['proof_url'], caption="Доказ вручення", use_container_width=True)
                
                if row.get('coordinates'):
                    coords = row['coordinates']
                    map_url = coords if "http" in coords else f"https://www.google.com/maps/search/?api=1&query={coords}"
                    st.markdown(f'<a href="{map_url}" target="_blank" style="display: block; text-align: center; background-color: #4285F4; color: white; padding: 10px; border-radius: 5px; text-decoration: none; font-weight: bold; margin-bottom: 10px;">🗺️ Відкрити в Навігаторі</a>', unsafe_allow_html=True)
                
                # --- БЛОК ВИДАЧІ: Безпечне використання камери ---
                if row['status'] != "Видано клієнту":
                    if st.session_state.active_camera != row['tracking_id']:
                        if st.button("📷 Відкрити камеру для видачі", key=f"btn_open_{row['tracking_id']}", width="stretch"):
                            st.session_state.active_camera = row['tracking_id']
                            st.rerun()
                    else:
                        photo = st.camera_input("📸 Сфотографувати посилку", key=f"cam_{row['tracking_id']}")
                        if st.button("❌ Скасувати", key=f"btn_cancel_{row['tracking_id']}", width="stretch"):
                            st.session_state.active_camera = None
                            st.rerun()
                        
                        if photo:
                            file_name = f"{row['tracking_id']}_{int(time.time())}.jpg"
                            supabase.storage.from_("proofs").upload(file_name, photo.getvalue(), {"content-type": "image/jpeg"})
                            photo_url = supabase.storage.from_("proofs").get_public_url(file_name)
                            
                            supabase.table("shipments").update({
                                "status": "Видано клієнту", 
                                "proof_url": photo_url
                            }).eq("tracking_id", row['tracking_id']).execute()
                            
                            st.session_state.active_camera = None # Скидаємо стан камери
                            st.success("✅ Видано та зафіксовано на фото!")
                            st.rerun()
                else:
                    st.success("✅ Ця посилка вже видана.")
    else:
        st.info("У цьому рейсі поки немає посилок.")

# ==========================================
# Вкладка 2: ПРИЙОМ НОВОЇ ПОСИЛКИ
# ==========================================
with tab2:
    st.subheader("Оформлення від клієнта")
    
    # 🔥 Підказка для водія щодо актуального тарифу
    st.info(f"💡 Поточний тариф: до {sys_settings['weight_threshold']}кг = £{sys_settings['min_price']}, понад = £{sys_settings['rate_per_kg']}/кг")
    
    location = streamlit_geolocation()
    
    if "my_coords" not in st.session_state:
        st.session_state.my_coords = ""
        
    # БЕЗПЕЧНЕ ОЧИЩЕННЯ
    if st.session_state.get("clear_my_coords"):
        st.session_state.my_coords = ""
        st.session_state.clear_my_coords = False

    if location and location.get('latitude') and location.get('longitude'):
        st.session_state.my_coords = f"{location['latitude']}, {location['longitude']}"
        st.success(f"✅ Локацію отримано: {st.session_state.my_coords}")
    elif location and 'latitude' in location and location['latitude'] is None:
        st.error("❌ Браузер заблокував GPS. Перевірте дозволи або використовуйте HTTPS.")
    else:
        st.info("ℹ️ Натисніть кнопку вище, щоб зчитати координати.")

    final_coords = st.text_input("Координати (GPS)", key="my_coords")

    with st.form("driver_new_package_form", clear_on_submit=True):
        st.markdown("**Відправник **")
        sender_name = st.text_input("ПІБ Відправника*")
        sender_phone = st.text_input("Телефон Відправника*", placeholder="+44...")
        
        st.markdown("**Отримувач **")
        recipient_name = st.text_input("ПІБ Отримувача*")
        recipient_phone = st.text_input("Телефон Отримувача*", placeholder="+380...")
        city_ua = st.text_input("Місто*", placeholder="Введіть місто")
        address = st.text_input("Точна адреса*")
        
        st.markdown("**Деталі посилки**")
        contents = st.text_input("Вміст (коротко)*")
        col3, col4 = st.columns(2)
        weight = col3.number_input("Вага (кг)", min_value=0.1, value=1.0)
        pkg_count = col4.number_input("Кількість місць", min_value=1, value=1)
        due_uah = st.number_input("Накладений платіж (Борг) ₴", min_value=0, value=0)

        submitted = st.form_submit_button("✅ Зберегти та Прийняти", width="stretch")
        
        if submitted:
            if not sender_name or not recipient_name or not city_ua or not address:
                st.error("❌ Будь ласка, заповніть всі обов'язкові поля з зірочкою (*)")
            else:
                # 🔥 ДОДАНО: Справедливий розрахунок вартості доставки
                if weight <= sys_settings["weight_threshold"]:
                    calculated_price_gbp = sys_settings["min_price"]
                else:
                    excess_weight = weight - sys_settings["weight_threshold"]
                    calculated_price_gbp = sys_settings["min_price"] + (excess_weight * sys_settings["rate_per_kg"])

                new_tracking_id = f"UKUA-{int(time.time())}"
                
                # 🔥 ДОДАНО: Запис параметра "price_gbp" у базу даних
                supabase.table("shipments").insert({
                    "tracking_id": new_tracking_id, "sender_uk": sender_name, "sender_phone": sender_phone,
                    "recipient_ua": recipient_name, "recipient_phone": recipient_phone, "city_ua": city_ua,
                    "address": address, 
                    "coordinates": final_coords, 
                    "contents": contents,
                    "package_count": pkg_count, "weight_kg": weight, "batch_id": my_batch,
                    "status": "Прийнято водієм", "due_uah": due_uah,
                    "price_gbp": calculated_price_gbp
                }).execute()
                
                st.success(f"Посилку прийнято! Трек: {new_tracking_id} | До сплати: £{calculated_price_gbp}")
                st.session_state.clear_my_coords = True
                time.sleep(1.5)
                st.rerun()