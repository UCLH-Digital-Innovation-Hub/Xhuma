FROM nginx:stable-alpine

# Remove default nginx configuration
RUN rm /etc/nginx/conf.d/default.conf

# Copy custom configuration
COPY azure.conf /etc/nginx/conf.d/

# Create directory for SSL certificates (they will be mounted at runtime)
RUN mkdir -p /etc/nginx/ssl

# Expose ports
EXPOSE 80 443

# Start Nginx
CMD ["nginx", "-g", "daemon off;"]
