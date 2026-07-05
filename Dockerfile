FROM python:3.11-slim

WORKDIR /app

# curl : requis par le healthcheck Coolify (GET /healthz depuis l'intérieur du conteneur)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# admin_ui.py (fichier d'origine, pensé pour un Pi avec systemd) appelle
# `systemctl`/`sudo` sans filet : absents en conteneur, ça fait planter
# la page d'accueil (FileNotFoundError). On fournit des stubs no-op pour
# que ces appels ne crashent plus (les boutons restart restent inertes
# en conteneur, comme documenté, mais sans faire tomber l'admin).
RUN printf '#!/bin/sh\ncase "$1" in\n  is-active) echo inactive ;;\nesac\nexit 0\n' > /usr/local/bin/systemctl \
    && printf '#!/bin/sh\nexec "$@"\n' > /usr/local/bin/sudo \
    && chmod +x /usr/local/bin/systemctl /usr/local/bin/sudo

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
