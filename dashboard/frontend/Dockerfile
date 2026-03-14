FROM node:20-alpine
WORKDIR /app

# API URL: leave empty so the frontend auto-detects from window.location.hostname
# This ensures the dashboard works from any IP/domain without rebuilding
ARG NEXT_PUBLIC_API_URL=
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build

ENV PORT=3001
CMD ["npm", "start"]
