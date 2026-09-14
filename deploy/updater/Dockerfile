FROM docker:27-cli
RUN apk add --no-cache python3
WORKDIR /updater
COPY tools/server_update.py ./tools/server_update.py
COPY packages/client/src/papagui_client/__init__.py ./packages/client/src/papagui_client/__init__.py
COPY packages/client/src/papagui_client/updates/__init__.py ./packages/client/src/papagui_client/updates/__init__.py
COPY packages/client/src/papagui_client/updates/feed.py ./packages/client/src/papagui_client/updates/feed.py
COPY packages/client/src/papagui_client/updates/storage.py ./packages/client/src/papagui_client/updates/storage.py
COPY LICENSE THIRD_PARTY_NOTICES.md ./licenses/
ENTRYPOINT ["python3", "tools/server_update.py"]
CMD ["--config", "/updater-config/updater.json", "--watch"]
