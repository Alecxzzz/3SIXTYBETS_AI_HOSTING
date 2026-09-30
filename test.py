from curl_cffi import requests
import json

# ID extraído del partido de mañana: Bélgica vs Francia
event_id = 12845610  
url = f"https://sofascore.com{event_id}/lineups"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*"
}

try:
    # Solicitud simulando la huella TLS de un navegador Chrome para evitar el 403 Forbidden
    response = requests.get(url, headers=headers, impersonate="chrome")
    
    if response.status_code == 200:
        data = response.json()
        
        # Comprobar si la UEFA ya confirmó oficialmente las alineaciones
        esta_confirmado = data.get("confirmed", False)
        estado_texto = "OFICIAL" if esta_confirmado else "PROBABLE (Predicción)"
        print(f"Estado de la alineación: {estado_texto}\n")
        
        # Procesar alineación del equipo local (Bélgica)
        print(f"--- ALINEACIÓN DE BÉLGICA (Formación: {data['home']['formation']}) ---")
        for jugador_data in data["home"]["players"]:
            # Filtramos para mostrar solo los que arrancan en la cancha (titulares)
            if not jugador_data.get("substitute", False):
                nombre = jugador_data["player"]["shortName"]
                pos = jugador_data["player"]["position"]
                print(f"[{pos}] {nombre}")
                
    else:
        print(f"Error en la API: Código de estado {response.status_code}")
except Exception as e:
    print(f"Ocurrió un error en la conexión: {e}")
