ARG NODE_IMAGE=node:22-alpine
ARG NGINX_IMAGE=nginx:alpine
FROM ${NODE_IMAGE} AS build
ARG NPM_REGISTRY=https://registry.npmjs.org
WORKDIR /app
RUN npm install --global pnpm@10.32.1 --registry="${NPM_REGISTRY}"
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
RUN pnpm install --frozen-lockfile --registry="${NPM_REGISTRY}"
COPY . .
ENV VUE_APP_API_BASE=/api NODE_OPTIONS=--max-old-space-size=768
RUN pnpm run build
FROM ${NGINX_IMAGE}
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
