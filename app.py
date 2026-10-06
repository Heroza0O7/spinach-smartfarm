import os
from typing import Optional

import pandas as pd
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from google import genai

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# พิกัดสำรอง (ใช้เมื่อผู้ใช้ไม่อนุญาตแชร์ตำแหน่ง) ตั้งค่าได้ใน Environment ของ Render
DEFAULT_LAT = float(os.getenv("FARM_LAT", "18.29"))
DEFAULT_LON = float(os.getenv("FARM_LON", "99.49"))
DEFAULT_CITY = os.getenv("FARM_CITY", "ลำปาง (ค่าเริ่มต้น)")

app = FastAPI()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=BASE_DIR)

SHEET_ID = "1oLkGf7t38hQBRw-x6VOoe7m7fDuT8lmakFsLuqBTiXg"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv"


def get_city_name(lat: float, lon: float) -> str:
    """แปลงพิกัดเป็นชื่อเมือง (reverse geocoding ฟรี ไม่ต้องใช้ key)"""
    try:
        geo = requests.get(
            "https://api.bigdatacloud.net/data/reverse-geocode-client",
            params={"latitude": lat, "longitude": lon, "localityLanguage": "th"},
            timeout=3,
        ).json()
        return (
            geo.get("city")
            or geo.get("locality")
            or geo.get("principalSubdivision")
            or f"{lat:.2f}, {lon:.2f}"
        )
    except Exception:
        return f"{lat:.2f}, {lon:.2f}"


def get_weather_forecast(lat: Optional[float] = None, lon: Optional[float] = None):
    try:
        if lat is None or lon is None:
            lat, lon, city = DEFAULT_LAT, DEFAULT_LON, DEFAULT_CITY
        else:
            city = get_city_name(lat, lon)

        url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,rain&daily=precipitation_probability_max&timezone=auto"
        )
        res = requests.get(url, timeout=5).json()

        return {
            "city": city,
            "temp": res["current"]["temperature_2m"],
            "rain_now": "มีฝนตก" if res["current"]["rain"] > 0 else "ไม่มีฝน",
            "rain_prob_today": res["daily"]["precipitation_probability_max"][0],
        }
    except Exception:
        return {"city": "N/A", "temp": "N/A", "rain_now": "ไม่ทราบ", "rain_prob_today": "N/A"}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/data")
async def get_data(lat: Optional[float] = None, lon: Optional[float] = None):
    try:
        df = pd.read_csv(CSV_URL)
        df["Moisture"] = pd.to_numeric(df["Moisture"], errors="coerce").fillna(0)
        df["pH_Value"] = pd.to_numeric(df["pH_Value"], errors="coerce").fillna(7.0)

        weather = get_weather_forecast(lat, lon)
        latest = {
            "ph": float(df["pH_Value"].iloc[-1]),
            "moisture": float(df["Moisture"].iloc[-1]),
            "timestamp": str(df["Timestamp"].iloc[-1]),
            "weather": weather,
        }
        return {"status": "success", "data": latest}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/analyze-stream")
async def analyze_stream(
    stage: str = "growth",
    lat: Optional[float] = None,
    lon: Optional[float] = None,
):
    def generate():
        try:
            df = pd.read_csv(CSV_URL)
            df["Moisture"] = pd.to_numeric(df["Moisture"], errors="coerce").fillna(0)
            df["pH_Value"] = pd.to_numeric(df["pH_Value"], errors="coerce").fillna(7.0)

            latest_ph = float(df["pH_Value"].iloc[-1])
            latest_moisture = float(df["Moisture"].iloc[-1])
            weather = get_weather_forecast(lat, lon)

            stage_info = {
                "seedling": "ระยะต้นกล้า (1-2 สัปดาห์) [ต้องการ pH 6.0-6.8, ความชื้น 70-80%]",
                "growth": "ระยะเร่งโต (3-4 สัปดาห์) [ต้องการ pH 6.0-7.0, ความชื้น 65-75%]",
                "harvest": "ระยะใกล้เก็บเกี่ยว (5 สัปดาห์ขึ้นไป) [ต้องการ pH 6.0-7.0, ความชื้น 55-65%]",
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
                model="gemini-3.6-flash",
                contents=prompt,
            )

            for chunk in response:
                if chunk.text:
                    yield chunk.text

        except Exception as e:
            yield f"เกิดข้อผิดพลาด: {str(e)}"

    return StreamingResponse(generate(), media_type="text/plain; charset=utf-8")