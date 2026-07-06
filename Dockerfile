# ─── Stage 1: Build Stage ────────────────
FROM eclipse-temurin:21-jdk AS builder
WORKDIR /build

# Copy Gradle wrapper configurations
COPY gradlew .
COPY gradle/ ./gradle/
COPY build.gradle .
COPY settings.gradle .

# Run Gradle task to cache dependency resolution (like dotnet restore)
# (This caches download layers so docker builds are very fast on subsequent runs)
RUN ./gradlew dependencies --no-daemon

# Copy source code and build the runnable boot JAR
COPY src/ ./src/
RUN ./gradlew bootJar -x test --no-daemon

# ─── Stage 2: Runtime Stage ──────────────
FROM eclipse-temurin:21-jre-jammy
WORKDIR /app

# Copy the compiled boot JAR from the builder stage
COPY --from=builder /build/build/libs/*.jar app.jar

# Create and run as a secure non-root user (UID 1000)
RUN useradd -u 1000 -m appuser
USER appuser

EXPOSE 8081

# Boot up the Spring Boot application
ENTRYPOINT ["java", "-jar", "app.jar"]