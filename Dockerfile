FROM python:3.12-alpine

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY main.py prom_api.py analyzer.py formatter.py logs.py history.py notify.py ./

# root 가 아닌 사용자로 실행
USER 10001

ENTRYPOINT ["python", "main.py"]
