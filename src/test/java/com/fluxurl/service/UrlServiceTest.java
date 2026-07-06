package com.fluxurl.service;

import com.fluxurl.model.Url;
import com.fluxurl.repository.UrlRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.Mockito;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.web.server.ResponseStatusException;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;

@ExtendWith(MockitoExtension.class)
public class UrlServiceTest {

    @Mock
    private UrlRepository urlRepository;

    @Mock
    private ShortnerService shortnerService;

    @InjectMocks
    private UrlService urlService;

    @Test
    public void testShortenUrl_SuccessOnFirstTry() {
        Mockito.when(shortnerService.generateShortCode()).thenReturn("abc1234");

        Url mockUrl = Url.builder().shortCode("abc1234").longUrl("https://example.com").build();
        Mockito.when(urlRepository.saveAndFlush(any(Url.class))).thenReturn(mockUrl);

        Url result = urlService.shortenUrl("https://example.com");

        assertThat(result.getShortCode()).isEqualTo("abc1234");
        Mockito.verify(urlRepository, Mockito.times(1)).saveAndFlush(any(Url.class));
    }

    @Test
    public void testShortenUrl_RecoversFromCollision() {
        Mockito.when(shortnerService.generateShortCode()).thenReturn("collis1", "collis2", "success");

        // First two saves throw a unique violation, third succeeds
        Mockito.when(urlRepository.saveAndFlush(any(Url.class)))
                .thenThrow(new DataIntegrityViolationException("Duplicate key"))
                .thenThrow(new DataIntegrityViolationException("Duplicate key"))
                .thenAnswer(invocation -> invocation.getArgument(0)); // Returns the Url passed to it

        Url result = urlService.shortenUrl("https://example.com");

        assertThat(result.getShortCode()).isEqualTo("success");
        // Verify we attempted to save 3 times
        Mockito.verify(urlRepository, Mockito.times(3)).saveAndFlush(any(Url.class));
    }

    @Test
    public void testShortenUrl_ThrowsExceptionOnExhaustion() {
        Mockito.when(shortnerService.generateShortCode()).thenReturn("code");

        // Mock saveAndFlush to always fail
        Mockito.when(urlRepository.saveAndFlush(any(Url.class)))
                .thenThrow(new DataIntegrityViolationException("Duplicate key"));

        assertThatThrownBy(() -> urlService.shortenUrl("https://example.com"))
                .isInstanceOf(ResponseStatusException.class)
                .hasMessageContaining("Could not generate a unique short code");

        // Verify we retried exactly 5 times
        Mockito.verify(urlRepository, Mockito.times(5)).saveAndFlush(any(Url.class));
    }
}