FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_OPERATOR_API_KEY
ARG VITE_REVIEWER_API_KEY
ENV VITE_OPERATOR_API_KEY=$VITE_OPERATOR_API_KEY \
    VITE_REVIEWER_API_KEY=$VITE_REVIEWER_API_KEY
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
