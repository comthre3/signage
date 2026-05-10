FROM nginx:1.27-alpine

ARG PLAYER_VERSION=dev
ENV PLAYER_VERSION=${PLAYER_VERSION}

COPY . /usr/share/nginx/html
COPY docker-entrypoint.sh /docker-entrypoint.sh
COPY nginx.conf /etc/nginx/conf.d/default.conf
RUN chmod +x /docker-entrypoint.sh

EXPOSE 80

ENTRYPOINT ["/docker-entrypoint.sh"]
