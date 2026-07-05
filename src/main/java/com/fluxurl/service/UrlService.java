package com.fluxurl.service;

import com.fluxurl.model.Url;
import com.fluxurl.repository.UrlRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;
import java.util.Optional;

@Service
@RequiredArgsConstructor
public class UrlService {

    private final UrlRepository urlRepository;
    private final ShortnerService shortnerService;
    private static final int MAX_COLLISION_RETRIES = 5;

    public Url shortenUrl(String longUrl) {
        for (int i = 0; i < MAX_COLLISION_RETRIES; i++) {
            String shortCode = shortnerService.generateShortCode();
            Url url = Url.builder()
                    .shortCode(shortCode)
                    .longUrl(longUrl)
                    .build();
            try {
                return urlRepository.saveAndFlush(url);
            } catch (DataIntegrityViolationException e) {
                // Unique constraint failed in DB, retry with another code
            }
        }

        throw new ResponseStatusException(
                HttpStatus.INTERNAL_SERVER_ERROR,
                "Could not generate a unique short code, please retry."
        );
    }

    public Optional<Url> getUrlByShortCode(String shortCode) {
        if (shortCode == null || !shortCode.matches("^[a-zA-Z0-9]{7}$")) {
            return Optional.empty();
        }

        return urlRepository.findById(shortCode);
    }
}