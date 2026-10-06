import os
import pandas as pd
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from google import genai

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

app = FastAPI()
templates = Jinja2Templates(directory="templates")

SHEET_ID = "1oLkGf7t38hQBRw-x6VOoe7m7fDuT8lmakFsLuqBTiXg"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv"

# ฟังก์ชันดึงพิกัดจาก IP และพยากรณ์อากาศจาก Open-Meteo
def get_weather_forecast():
    try:
        # 1. ดึงพิกัดและชื่อเมืองจาก IP Address
        ip_res = requests.get("http://ip-api.com/json/", timeout=3).json()
        
        if ip_res.get("status") == "success":
            lat = ip_res.get("lat")
            lon = ip_res.get("lon")
            city = ip_res.get("city", "ไม่ระบุพื้นที่")
        else:
            lat, lon, city = 18.79, 98.98, "เชียงใหม่ (ค่าเริ่มต้น)"

        # 2. นำพิกัดดึงพยากรณ์อากาศจาก Open-Meteo
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,rain&daily=precipitation_probability_max&timezone=auto"
        res = requests.get(url, timeout=3).json()
        
        return {
            "city": city,
            "temp": res['current']['temperature_2m'],
            "rain_now": "มีฝนตก" if res['current']['rain'] > 0 else "ไม่มีฝน",
            "rain_prob_today": res['daily']['precipitation_probability_max'][0]
        }
    except Exception:
        return {"city": "N/A", "temp": "N/A", "rain_now": "ไม่ทราบ", "rain_prob_today": "N/A"}

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/data")
async def get_data():
    try:
        df = pd.read_csv(CSV_URL)
        df['Moisture'] = pd.to_numeric(df['Moisture'], errors='coerce').fillna(0)
        df['pH_Value'] = pd.to_numeric(df['pH_Value'], errors='coerce').fillna(7.0)
        
        weather = get_weather_forecast()
        latest = {
            "ph": float(df['pH_Value'].iloc[-1]),
            "moisture": float(df['Moisture'].iloc[-1]),
            "timestamp": str(df['Timestamp'].iloc[-1]),
            "weather": weather
        }
        return {"status": "success", "data": latest}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/analyze-stream")
async def analyze_stream(stage: str = "growth"):
    def generate():
        try:
            df = pd.read_csv(CSV_URL)
            df['Moisture'] = pd.to_numeric(df['Moisture'], errors='coerce').fillna(0)
            df['pH_Value'] = pd.to_numeric(df['pH_Value'], errors='coerce').fillna(7.0)
            
            latest_ph = float(df['pH_Value'].iloc[-1])
            latest_moisture = float(df['Moisture'].iloc[-1])
            weather = get_weather_forecast()

            stage_info = {
                "seedling": "ระยะต้นกล้า (1-2 สัปดาห์) [ต้องการ pH 6.0-6.8, ความชื้น 70-80%]",
                "growth": "ระยะเร่งโต (3-4 สัปดาห์) [ต้องการ pH 6.0-7.0, ความชื้น 65-75%]",
                "harvest": "ระยะใกล้เก็บเกี่ยว (5 สัปดาห์ขึ้นไป) [ต้องการ pH 6.0-7.0, ความชื้น 55-65%]"
            }.get(stage, "ระยะเติบโตทั่วไป")

            client = genai.Client(api_key=GEMINI_API_KEY)
            prompt = f"""
            คุณคือผู้เชี่ยวชาญด้านการปลูกผักโขม (Spinach)
            
            📌 ระยะการปลูก: {stage_info}
            📊 เซนเซอร์ในดิน: pH = {latest_ph}, ความชื้น = {latest_moisture}%
            🌤️ อากาศในพื้นที่ [{weather['city']}]: อุณหภูมิ {weather['temp']}°C, โอกาสฝนตกวันนี้ {weather['rain_prob_today']}%

            วิเคราะห์และสรุปคำแนะนำเป็นข้อๆ สั้น กระชับ:
            1. ความเหมาะสมของสภาพดินในระยะนี้
            2. คำแนะนำการรดน้ำ (พิจารณาฝนตก + ความชื้นดินในพื้นที่ {weather['city']})
            3. ข้อควรระวังพิเศษสำหรับระยะนี้
            """

            response = client.models.generate_content_stream(
                model='gemini-3.6-flash',
                contents=prompt
            )

            for chunk in response:
                if chunk.text:
                    yield chunk.text

        except Exception as e:
            yield f"เกิดข้อผิดพลาด: {str(e)}"

    return StreamingResponse(generate(), media_type="text/plain; charset=utf-8")