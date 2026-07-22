import time
import base64
import requests
from io import BytesIO
from datetime import date
import pandas as pd
import altair as alt
import streamlit as st
import streamlit.components.v1 as components
import barcode
from barcode.writer import ImageWriter
from supabase import create_client, Client
from streamlit_geolocation import streamlit_geolocation

st.set_page_config(page_title="Logistics ERP | Office", layout="wide", page_icon="📦")

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

# 🔥 ФУНКЦІЯ ОТРИМАННЯ АКТУАЛЬНИХ КУРСІВ ВАЛЮТ (Кешується на 1 годину)
@st.cache_data(ttl=3600)
def fetch_exchange_rates():
    try:
        response = requests.get("https://open.er-api.com/v6/latest/GBP").json()
        return response.get("rates", {"GBP": 1, "UAH": 53.0, "EUR": 1.17, "PLN": 5.0})
    except:
        return {"GBP": 1, "UAH": 53.0, "EUR": 1.17, "PLN": 5.0} # Резервні курси на випадок відсутності інтернету

def upsert_client(phone, name, city="", address="", coords=""):
    if not phone or not name: return
    existing = supabase.table("clients").select("*").eq("phone", phone).execute()
    payload = {"phone": phone, "full_name": name}
    if city: payload["city"] = city
    if address: payload["address"] = address
    if coords: payload["coordinates"] = coords
    
    if existing.data:
        supabase.table("clients").update(payload).eq("phone", phone).execute()
    else:
        supabase.table("clients").insert(payload).execute()

st.title("🖥️ ERP Офіс (Логістика UK-UA)")

menu = st.sidebar.radio(
    "Головне меню", 
    ["📊 Статистика та Фінанси", "📅 Планувальник рейсів", "➕ Нова посилка", "📦 База посилок", "👥 База клієнтів", "🖨️ Друк стікерів", "⚙️ Налаштування тарифів"]
)

# ==========================================
# 0. СТАТИСТИКА, ФІНАНСИ ТА МИТНИЦА
# ==========================================
if menu == "📊 Статистика та Фінанси":
    rates = fetch_exchange_rates()
    
    st.subheader("Фінансовий Дашборд")
    st.markdown(f"**💱 Живі курси валют (база £1 GBP):** ₴ {rates.get('UAH', 0):.2f} UAH &nbsp;|&nbsp; € {rates.get('EUR', 0):.2f} EUR &nbsp;|&nbsp; {rates.get('PLN', 0):.2f} PLN")
    
    tab1, tab2, tab3 = st.tabs(["📈 Статистика Рейсу (Batch)", "💰 Додати Витрати", "🌍 Загальна (Lifetime)"])
    
    with tab1:
        batches_req = supabase.table("batches").select("batch_id").execute()
        b_list = [b['batch_id'] for b in batches_req.data] if batches_req.data else []
        
        if b_list:
            selected_batch = st.selectbox("Оберіть рейс для аналізу", b_list)
            
            s_req = supabase.table("shipments").select("*").eq("batch_id", selected_batch).execute()
            s_df = pd.DataFrame(s_req.data)
            
            e_req = supabase.table("expenses").select("amount_gbp, category, description").eq("batch_id", selected_batch).execute()
            e_df = pd.DataFrame(e_req.data)
            
            revenue = s_df['price_gbp'].sum() if not s_df.empty else 0
            total_weight = s_df['weight_kg'].sum() if not s_df.empty else 0
            
            # Конвертація боргу (COD) з Гривень у Фунти для оцінки
            cod_uah = s_df['due_uah'].sum() if not s_df.empty else 0
            cod_equiv_gbp = round(cod_uah / rates.get('UAH', 1), 2)
            
            expenses = e_df['amount_gbp'].sum() if not e_df.empty else 0
            profit = revenue - expenses
            
            st.markdown("### 🧮 Каса водія (Баланс у бусі)")
            st.info(f"**Зібрав готівки (COD):** ₴{cod_uah} *(еквівалент ≈ £{cod_equiv_gbp})*  |  **Витратив своїх коштів:** £{expenses}")
            
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("📦 Дохід від доставки", f"£{revenue}")
            col2.metric("⛽ Витрати на рейс", f"£{expenses}")
            col3.metric("💎 Чистий прибуток", f"£{profit}", delta=profit)
            col4.metric("⚖️ Загальна вага", f"{total_weight} кг")
            
            if not s_df.empty:
                st.markdown("### 🖨 Митна декларація")
                manifest_df = s_df[['tracking_id', 'sender_uk', 'recipient_ua', 'contents', 'package_count', 'weight_kg']]
                manifest_df.columns = ['Трек-номер', 'Відправник', 'Отримувач', 'Вміст', 'Місць', 'Вага (кг)']
                output = BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer: manifest_df.to_excel(writer, index=False, sheet_name='Manifest')
                st.download_button("📥 Завантажити Excel для митниці", data=output.getvalue(), file_name=f"Manifest_{selected_batch}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
            
            if not e_df.empty:
                st.write("**Деталізація витрат по цьому рейсу:**")
                st.dataframe(e_df, width="stretch")
        else:
            st.info("Немає рейсів для аналізу.")

    with tab2:
        st.markdown("**Реєстрація витрат (Мультивалютна)**")
        with st.form("expense_form", clear_on_submit=True):
            e_date = st.date_input("Дата")
            e_cat = st.selectbox("Категорія", ["⛽ Пальне", "👤 Зарплата водія", "🚢 Пором / Тунель", "🛠 Ремонт / ТО", "🏢 Інше"])
            
            # 🔥 МУЛЬТИВАЛЮТНИЙ ВВІД
            col_amt, col_cur = st.columns([2, 1])
            e_amount_local = col_amt.number_input("Сума*", min_value=0.1, value=50.0)
            e_curr = col_cur.selectbox("Валюта чеку", ["GBP", "EUR", "PLN", "UAH"])
            
            e_desc = st.text_input("Опис", placeholder="Напр: Заправка Orlen в Польщі")
            e_batch = st.selectbox("Прив'язати до рейсу", ["Загальні витрати"] + b_list)
            
            if st.form_submit_button("💾 Зберегти витрату", width="stretch"):
                # Автоматична конвертація
                rate = rates.get(e_curr, 1)
                calc_gbp = round(e_amount_local / rate, 2)
                
                # Додаємо оригінальну суму в опис, якщо це не фунти
                final_desc = f"{e_desc} ({e_amount_local} {e_curr})" if e_curr != "GBP" else e_desc
                batch_val = "" if e_batch == "Загальні витрати" else e_batch
                
                supabase.table("expenses").insert({
                    "date": str(e_date), "category": e_cat, 
                    "amount_gbp": calc_gbp, "description": final_desc, "batch_id": batch_val
                }).execute()
                st.success(f"Витрату збережено! Автоматично конвертовано: £{calc_gbp}")
                time.sleep(1.5)
                st.rerun()

    with tab3:
        st.markdown("**Lifetime Статистика (За весь час)**")
        all_s_req = supabase.table("shipments").select("*").execute()
        all_s = pd.DataFrame(all_s_req.data)
        all_e = pd.DataFrame(supabase.table("expenses").select("amount_gbp, batch_id").execute().data)
        
        if not all_s.empty:
            tot_rev = all_s['price_gbp'].sum()
            tot_exp = all_e['amount_gbp'].sum() if not all_e.empty else 0
            
            c1, c2, c3 = st.columns(3)
            c1.metric("Усього відправлень", f"{len(all_s)} шт")
            c2.metric("Загальний обіг (Revenue)", f"£{tot_rev}")
            c3.metric("Загальний чистий прибуток", f"£{tot_rev - tot_exp}", delta=f"-£{tot_exp} витрат")
            st.divider()
            colA, colB = st.columns(2)
            with colA:
                st.markdown("#### 📈 Доходи по рейсах (£)")
                bar_data = all_s.groupby("batch_id")["price_gbp"].sum().reset_index()
                st.bar_chart(bar_data.set_index("batch_id"))
            with colB:
                st.markdown("#### 🎯 Популярні міста (Топ напрямків)")
                city_counts = all_s['city_ua'].value_counts().reset_index()
                city_counts.columns = ['Місто', 'Кількість']
                pie_chart = alt.Chart(city_counts).mark_arc(innerRadius=50).encode(theta=alt.Theta(field="Кількість", type="quantitative"), color=alt.Color(field="Місто", type="nominal"), tooltip=['Місто', 'Кількість']).interactive()
                st.altair_chart(pie_chart, use_container_width=True)

# ==========================================
# 1. ПЛАНУВАЛЬНИК РЕЙСІВ
# ==========================================
elif menu == "📅 Планувальник рейсів":
    st.subheader("Керування маршрутами та розкладом")
    col1, col2 = st.columns([1, 2])
    with col1:
        with st.form("new_batch_form", clear_on_submit=True):
            st.markdown("**Створити новий рейс**")
            b_id = st.text_input("ID Рейсу*", placeholder="Напр. LON-KYI-2808")
            route = st.text_input("Маршрут", placeholder="Лондон - Київ")
            d_date = st.date_input("Дата виїзду")
            if st.form_submit_button("📅 Додати в розклад", width='stretch'):
                supabase.table("batches").insert({"batch_id": b_id, "route_name": route, "departure_day": str(d_date), "status": "Заплановано"}).execute()
                st.success("Рейс заплановано!")
                st.rerun()
    with col2:
        batches_req = supabase.table("batches").select("*").neq("status", "Завершено").order("departure_day").execute()
        batches_df = pd.DataFrame(batches_req.data)
        if not batches_df.empty:
            for _, row in batches_df.iterrows():
                is_overdue = date.today() > pd.to_datetime(row['departure_day']).date()
                box_color = "border: 2px solid #e31837; background-color: #ffe6e6;" if is_overdue else "border: 1px solid #ddd;"
                st.markdown(f'<div style="{box_color} padding: 15px; border-radius: 8px; margin-bottom: 10px;"><h4>🚚 {row["batch_id"]} ({row["route_name"]})</h4><p><b>Дата виїзду:</b> {row["departure_day"]} | <b>Статус:</b> {row["status"]}</p></div>', unsafe_allow_html=True)
                if is_overdue: st.warning("⏳ Дата виїзду минула. Підтвердіть завершення рейсу!")
                if st.button(f"✅ Примусово завершити {row['batch_id']}", key=f"btn_{row['batch_id']}", width="stretch"):
                    supabase.table("batches").update({"status": "Завершено"}).eq("batch_id", row['batch_id']).execute()
                    st.rerun()

# ==========================================
# 2. НОВА ПОСИЛКА 
# ==========================================
elif menu == "➕ Нова посилка":
    st.subheader("Реєстрація відправлення")
    st.info(f"💡 Тариф: до {sys_settings['weight_threshold']}кг = £{sys_settings['min_price']}, понад = £{sys_settings['rate_per_kg']}/кг")
    
    active_batches = supabase.table("batches").select("batch_id").neq("status", "Завершено").execute()
    batch_options = [b['batch_id'] for b in active_batches.data] if active_batches.data else ["Немає активних рейсів"]
    
    st.markdown("### 🔍 Швидкий пошук клієнта")
    clients_req = supabase.table("clients").select("*").execute()
    clients_list = clients_req.data if clients_req.data else []
    client_options = ["--- Ввести вручну ---"] + [f"{c['phone']} ({c['full_name']})" for c in clients_list]
    
    col_search1, col_search2 = st.columns(2)
    with col_search1:
        selected_sender = st.selectbox("Автозаповнення Відправника", client_options)
        s_data = next((c for c in clients_list if f"{c['phone']} ({c['full_name']})" == selected_sender), {})
    with col_search2:
        selected_recipient = st.selectbox("Автозаповнення Отримувача", client_options)
        r_data = next((c for c in clients_list if f"{c['phone']} ({c['full_name']})" == selected_recipient), {})

    st.markdown("""<style>iframe[title*="streamlit_geolocation"] { transform: scale(1.3); transform-origin: top left; height: 65px !important; }</style>""", unsafe_allow_html=True)
    location = streamlit_geolocation()
    if "office_coords" not in st.session_state: st.session_state.office_coords = ""
    if st.session_state.get("clear_office_coords"):
        st.session_state.office_coords = ""
        st.session_state.clear_office_coords = False
        
    if location and location.get('latitude') and location.get('longitude'):
        st.session_state.office_coords = f"{location['latitude']}, {location['longitude']}"
        st.success("✅ Локацію отримано")

    final_coords = st.text_input("Координати (GPS) або лінк", key="office_coords")

    with st.form("new_package_form", clear_on_submit=True):
        batch_id = st.selectbox("Прив'язати до рейсу", batch_options)
        col_c, col_a = st.columns(2)
        city_ua = col_c.text_input("Місто (введіть будь-яке)*", value=r_data.get("city", ""))
        address = col_a.text_input("Точна адреса*", value=r_data.get("address", ""))
        col1, col2 = st.columns(2)
        sender_name = col1.text_input("Відправник *", value=s_data.get("full_name", ""))
        sender_phone = col1.text_input("Телефон Відправника*", value=s_data.get("phone", ""))
        recipient_name = col2.text_input("Отримувач *", value=r_data.get("full_name", ""))
        recipient_phone = col2.text_input("Телефон Отримувача*", value=r_data.get("phone", ""))
        
        contents = st.text_input("Вміст (для митниці)*")
        col3, col4, col5, col6 = st.columns(4)
        weight = col3.number_input("Вага (кг)*", min_value=0.1, value=1.0)
        pkg_count = col4.number_input("Місць", min_value=1, value=1)
        due_uah = col5.number_input("Борг ₴", min_value=0, value=0)
        box_number = col6.text_input("№ Ящика (пусто=авто)")

        if st.form_submit_button("✅ Зберегти в базу", width='stretch'):
            if not sender_name or not recipient_name or not city_ua or not address:
                st.error("❌ Помилка: Заповніть обов'язкові поля")
            else:
                if weight <= sys_settings["weight_threshold"]: calculated_price_gbp = sys_settings["min_price"]
                else: calculated_price_gbp = sys_settings["min_price"] + ((weight - sys_settings["weight_threshold"]) * sys_settings["rate_per_kg"])

                if not box_number:
                    existing_pkgs = supabase.table("shipments").select("id").eq("batch_id", batch_id).execute()
                    box_suffix = str(len(existing_pkgs.data) + 1 if existing_pkgs.data else 1)
                else:
                    box_suffix = str(box_number).strip()

                base_trk = batch_id if batch_id != "Немає активних рейсів" else f"UKUA-{int(time.time())}"
                new_tracking_id = f"{base_trk}/{box_suffix}"

                upsert_client(sender_phone, sender_name) 
                upsert_client(recipient_phone, recipient_name, city_ua, address, final_coords)

                supabase.table("shipments").insert({
                    "tracking_id": new_tracking_id, "batch_id": batch_id,
                    "city_ua": city_ua, "address": address, "coordinates": final_coords, 
                    "sender_uk": sender_name, "sender_phone": sender_phone,
                    "recipient_ua": recipient_name, "recipient_phone": recipient_phone,
                    "contents": contents, "weight_kg": weight, "package_count": pkg_count,
                    "due_uah": due_uah, "price_gbp": calculated_price_gbp, "status": "Оформлено в офісі"
                }).execute()
                
                st.success(f"Відправлення збережено! Ваш Трек-номер: {new_tracking_id}")
                st.session_state.clear_office_coords = True
                time.sleep(1.5)
                st.rerun()

# ==========================================
# 3. БАЗА ПОСИЛОК
# ==========================================
elif menu == "📦 База посилок":
    st.subheader("Керування даними")
    shipments_req = supabase.table("shipments").select("*").order("id", desc=True).execute()
    df = pd.DataFrame(shipments_req.data)
    if not df.empty: st.dataframe(df, width='stretch')
    else: st.info("База порожня.")

# ==========================================
# 4. БАЗА КЛІЄНТІВ
# ==========================================
elif menu == "👥 База клієнтів":
    st.subheader("Довідник клієнтів (CRM)")
    clients_req = supabase.table("clients").select("*").execute()
    if clients_req.data: st.dataframe(pd.DataFrame(clients_req.data), width="stretch")
    else: st.info("У вас ще немає збережених клієнтів.")

# ==========================================
# 5. ДРУК СТІКЕРІВ
# ==========================================
elif menu == "🖨️ Друк стікерів":
    st.subheader("Генератор логістичних етикеток")
    shipments_req = supabase.table("shipments").select("*").order("id", desc=True).execute()
    df = pd.DataFrame(shipments_req.data)
    
    if not df.empty:
        selected_id = st.selectbox("Оберіть Tracking ID для друку", df["tracking_id"])
        row = df[df["tracking_id"] == selected_id].iloc[0]

        def generate_barcode_base64(text):
            rv = BytesIO()
            code = barcode.get("code128", text, writer=ImageWriter())
            code.write(rv, options={"module_width": 0.35, "module_height": 12.0, "font_size": 0, "quiet_zone": 2})
            return base64.b64encode(rv.getvalue()).decode("utf-8")

        barcode_base64 = generate_barcode_base64(row["tracking_id"])
        cod_html = f"<br><b>ДО СПЛАТИ: ₴{row['due_uah']}</b>" if row['due_uah'] > 0 else ""
        sticker_html = f"""
        <!DOCTYPE html><html><head><style>
            body {{ font-family: Arial, sans-serif; background-color: transparent; margin: 0; padding: 10px; }}
            .sticker-container {{ border: 3px solid #000; padding: 15px; width: 400px; background: #fff; color: #000; box-sizing: border-box; }}
            .header {{ font-weight: bold; background: #000; color: white; padding: 6px; text-align: center; font-size: 14px; margin-bottom: 10px; }}
            .print-btn {{ background-color: #e31837; color: white; border: none; padding: 10px 15px; font-size: 14px; font-weight: bold; border-radius: 4px; cursor: pointer; margin-top: 15px; width: 100%; }}
            @media print {{ body * {{ visibility: hidden; }} .sticker-container, .sticker-container * {{ visibility: visible; }} .sticker-container {{ position: absolute; left: 0; top: 0; width: 100%; border: none; }} .print-btn {{ display: none; }} }}
        </style></head><body>
        <div class="sticker-container"><div class="header">NOVA POST | UK ➔ UA</div>
            <div style="font-size: 12px; margin-bottom: 5px;"><b>TRACKING:</b> {row['tracking_id']}</div>
            <div style="font-size: 12px; margin-bottom: 8px;"><b>SENDER:</b> {row['sender_uk']} ({row.get('sender_phone', '')})</div>
            <hr style="border: 1px solid #000; margin: 8px 0;">
            <div style="text-align: center; margin: 8px 0;"><img src="data:image/png;base64,{barcode_base64}" style="max-width: 100%; height: auto;" alt="Barcode"><div style="font-size: 11px; font-family: monospace; font-weight: bold;">*{row['tracking_id']}*</div></div>
            <hr style="border: 1px solid #000; margin: 8px 0;">
            <div style="font-size: 12px; line-height: 1.4;"><b>RECIPIENT:</b> {row['recipient_ua']}<br><b>PHONE:</b> {row.get('recipient_phone', '')}<br><b>ADDRESS:</b> {row['city_ua']}, {row['address']}<br><b>CONTENTS:</b> {row['contents']}<br><b>PACKAGES:</b> {row['package_count']} | <b>WEIGHT:</b> {row['weight_kg']} kg {cod_html}</div>
            <button class="print-btn" onclick="window.print()">🖨️ Друк етикетки</button>
        </div></body></html>"""
        components.html(sticker_html, height=520)
    else: st.info("Спочатку додайте посилки в базу для друку.")

# ==========================================
# 6. НАЛАШТУВАННЯ ТАРИФІВ
# ==========================================
elif menu == "⚙️ Налаштування тарифів":
    st.subheader("Глобальні налаштування цін")
    with st.form("settings_form"):
        col1, col2, col3 = st.columns(3)
        new_min_weight = col1.number_input("Поріг ваги (кг)", value=float(sys_settings["weight_threshold"]))
        new_min_price = col2.number_input("Фіксована ціна до порогу (£)", value=float(sys_settings["min_price"]))
        new_rate = col3.number_input("Тариф за 1 кг (понад поріг) (£)", value=float(sys_settings["rate_per_kg"]))
        if st.form_submit_button("💾 Зберегти нові тарифи", type="primary"):
            supabase.table("settings").update({"weight_threshold": new_min_weight, "min_price": new_min_price, "rate_per_kg": new_rate}).eq("id", 1).execute()
            st.success("Тарифи успішно оновлено!")
            st.rerun()