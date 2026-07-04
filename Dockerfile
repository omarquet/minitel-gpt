FROM python:3.11-slim

WORKDIR /app

# Dependances (pas de compilation lourde : tout est pur Python)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Code du projet (services/, config/, assets/, server.py ...)
COPY . /app

# Copie de reference de la config pour amorcer le volume persistant au 1er boot
RUN cp -r /app/config /app/seed-config && mkdir -p /app/logs

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
# 1 worker, plusieurs threads : chaque Minitel connecte occupe un thread (boucle
# de chat synchrone). gthread convient a flask-sock pour un usage hobby.
CMD ["gunicorn", "-k", "gthread", "-w", "1", "--threads", "8", \
     "--timeout", "0", "-b", "0.0.0.0:8080", \
     "--chdir", "/app/services", "server:app"]
