package com.fluxurl.controller;

import com.fluxurl.dto.ShortenRequest;
import com.fluxurl.dto.ShortenResponse;
import com.fluxurl.model.Url;
import com.fluxurl.repository.UrlRepository;
import com.fluxurl.service.ShortnerService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

@RestController
@RequiredArgsConstructor
public class UrlController {
    private final UrlRepository urlRepository;
    private final ShortnerService shortnerService;

    @Value("${app.base-url}")
    private String baseUrl;

    @PostMapping("/shorten")
    public ResponseEntity<ShortenResponse> shorten(@Valid @RequestBody ShortenRequest payload) {
        String longUrl = payload.getUrl();
        int maxRetries = 3;

        for(int i=0; i<maxRetries; i++) {
            String shortCode = shortnerService.generateShortCode();
            Url url = Url.builder()
                    .shortCode(shortCode)
                    .longUrl(longUrl)
                    .build();

            try{
                urlRepository.saveAndFlush(url);

                ShortenResponse response = ShortenResponse.builder()
                        .shortCode(shortCode)
                        .shortUrl(baseUrl + "/" + shortCode)
                        .longUrl(longUrl)
                        .build();

                return ResponseEntity.status(HttpStatus.CREATED).body(response);
            } catch(DataIntegrityViolationException e) {
                // Collision occurred, Unique key constraint failed in DB.
            }
        }

        throw new ResponseStatusException(
                HttpStatus.INTERNAL_SERVER_ERROR,
                "Could not generate a unique short code, please try again."
        );
    }

    @GetMapping("/{shortCode}")
    public ResponseEntity<Void> redirectToLongUrl(@PathVariable String shortCode) {
        if (shortCode == null || !shortCode.matches("^[a-zA-Z0-9]{7}$")) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "short code not found");
        }

        Url url = urlRepository.findById(shortCode)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "short code not found"));

        return ResponseEntity.status(HttpStatus.FOUND)
                .header(HttpHeaders.LOCATION, url.getLongUrl())
                .build();
    }
}
