FROM alpine:3.21

COPY custom_components/llama_conversation /edgehome/llama_conversation
COPY knowledge /edgehome/knowledge
COPY datasets/energy /edgehome/energy

ENTRYPOINT ["sh", "-c", "mkdir -p /config/custom_components /config/edgehome_knowledge /config/edgehome_energy && rm -rf /config/custom_components/llama_conversation /config/edgehome_knowledge/* /config/edgehome_energy/* && cp -R /edgehome/llama_conversation /config/custom_components/llama_conversation && cp -R /edgehome/knowledge/. /config/edgehome_knowledge && cp -R /edgehome/energy/. /config/edgehome_energy"]
