FROM alpine:3.21

COPY custom_components/llama_conversation /edgehome/llama_conversation

ENTRYPOINT ["sh", "-c", "mkdir -p /config/custom_components && rm -rf /config/custom_components/llama_conversation && cp -R /edgehome/llama_conversation /config/custom_components/llama_conversation"]
