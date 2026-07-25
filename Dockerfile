FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# MCP servers are stdio by default. In a container we expose an HTTP transport
# so clients can reach it over the network (set MCP_TRANSPORT=sse as needed).
ENV MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

EXPOSE 8000

CMD ["python", "main.py"]
