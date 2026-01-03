He copiado la Curl de "players" en el inspector > red > fetch/XHR

curl 'https://api.futmondo.com/1/market/players' \
  -H 'Accept: */*' \
  -H 'Accept-Language: es-ES,es;q=0.5' \
  -H 'Connection: keep-alive' \
  -H 'Content-Type: application/json; charset=utf-8' \
  -H 'Origin: https://app.futmondo.com' \
  -H 'Referer: https://app.futmondo.com/' \
  -H 'Sec-Fetch-Dest: empty' \
  -H 'Sec-Fetch-Mode: cors' \
  -H 'Sec-Fetch-Site: same-site' \
  -H 'Sec-GPC: 1' \
  -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36' \
  -H 'sec-ch-ua: "Not;A=Brand";v="99", "Brave";v="139", "Chromium";v="139"' \
  -H 'sec-ch-ua-mobile: ?0' \
  -H 'sec-ch-ua-platform: "macOS"' \
  --data-raw '{"header":{"token":"b512_1a9c26ae268684329edd237c2194f385","userid":"55a8a8bd0c9a7189021b2c34"},"query":{"championshipId":"64f45b87f0ee1105e1ea0e9a","userteamId":"64f49cfa016d860e1faabe35","type":"market"},"answer":{}}'

url para iniciar sesión: https://app.futmondo.com/#login
api endpoint para iniciar sesión https://api.futmondo.com/5/login/with_mail


 python3 scraper.py 
/Users/macmini/Public/fantasy_futmondo/scrap_futmondo/.venv/lib/python3.9/site-packages/urllib3/__init__.py:35: NotOpenSSLWarning: urllib3 v2 only supports OpenSSL 1.1.1+, currently the 'ssl' module is compiled with 'LibreSSL 2.8.3'. See: https://github.com/urllib3/urllib3/issues/3020
  warnings.warn(
Iniciando sesión a través de la API...
Inicio de sesión exitoso. Analizando respuesta...
{
  "answer": {
    "code": "api.general.ok",
    "mobile": {
      "code": "login.mobile.ok",
      "token": "a5ce_60c18117f642f7db623a003ed3549d96",
      "userid": "55a8a8bd0c9a7189021b2c34"
    }
  },
  "query": {
    "mail": "sergio@gmail.com",
    "pwd": "password"
  },
  "header": {
    "token": null,
    "userid": ""
  }
}
Error: No se pudo obtener el token de sesión o el userid de la respuesta de login.
La estructura de la respuesta de la API puede haber cambiado.
(.venv) Mac-mini-de-Sergio:scrap_futmondo macmini$ 

https://api.telegram.org/botTOKEN/getUpdates

8111396853:AAEhlIwhDUsbajI_RCj_gLx_jGrhv8483kI

https://api.telegram.org/bot8111396853:AAEhlIwhDUsbajI_RCj_gLx_jGrhv8483kI/getUpdates

Crear ETL para guardar en base de datos el mercado.
¿qué campos quiero guardar?
"id"
"name"
"role"
"points"
"value"
"team"
"creationDate"
"expirationDate"
"price"
"computer"
"change"
"average"
"numberOfBids"


Si computer false, guarda también el campo 
"userTeam"

para poder cruzar con presroom se puede usar el id del jugador y presroom.created = mercado.expirationDate hay 30 minutos de diferencia entre ambos

resultado chatgpt con el que hago este proceso:
https://chatgpt.com/share/68bfc911-ea08-8002-9741-251b01c564fb

