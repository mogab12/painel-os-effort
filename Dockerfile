# Imagem oficial do Playwright: já vem com o Chromium e as dependências do sistema.
# Mantenha a versão da imagem igual à do pacote playwright do requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=America/Sao_Paulo
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
