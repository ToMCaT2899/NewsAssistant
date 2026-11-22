ARG NODE_BASE_IMAGE=node:24-alpine
ARG NGINX_BASE_IMAGE=nginx@sha256:5ab5b0b01097420873a3f3330f44248e34a3c2a3e8d5ee30658aeae8cb7435da
FROM ${NODE_BASE_IMAGE} AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run lint && npm run format:check && npm run build

FROM ${NGINX_BASE_IMAGE} AS runtime
COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build /web/dist /usr/share/nginx/html
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
