package com.fluxurl.controller;

import com.fluxurl.model.Url;
import com.fluxurl.service.UrlService;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import java.util.Optional;
import static org.mockito.ArgumentMatchers.any;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@WebMvcTest(UrlController.class)
public class UrlControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private UrlService urlService;

    @Test
    public void testShortenUrl_Success() throws Exception {
        Url mockUrl = Url.builder()
                .shortCode("abcdefg")
                .longUrl("https://example.com")
                .build();

        // Stub: Mock the service method (like Moq's Setup method)
        Mockito.when(urlService.shortenUrl("https://example.com")).thenReturn(mockUrl);

        // Perform request and verify
        mockMvc.perform(post("/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"url\":\"https://example.com\"}"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.shortCode").value("abcdefg"))
                .andExpect(jsonPath("$.longUrl").value("https://example.com"))
                // Expect Base URL from application.properties (default is http://localhost:8081)
                .andExpect(jsonPath("$.shortUrl").value("http://localhost:8081/abcdefg"));
    }

    @Test
    public void testShortenUrl_ValidationFailure() throws Exception {
        // Post an invalid URL structure and check for 422 Unprocessable Entity
        mockMvc.perform(post("/shorten")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"url\":\"invalid-url\"}"))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.detail").exists());
    }

    @Test
    public void testRedirect_Success() throws Exception {
        Url mockUrl = Url.builder()
                .shortCode("abcdefg")
                .longUrl("https://example.com")
                .build();

        Mockito.when(urlService.getUrlByShortCode("abcdefg")).thenReturn(Optional.of(mockUrl));

        // Expect HTTP 302 (Found) and Location header
        mockMvc.perform(get("/abcdefg"))
                .andExpect(status().isFound())
                .andExpect(header().string("Location", "https://example.com"));
    }

    @Test
    public void testRedirect_NotFound() throws Exception {
        Mockito.when(urlService.getUrlByShortCode("notfnd7")).thenReturn(Optional.empty());

        mockMvc.perform(get("/notfnd7"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.detail").value("short code not found"));
    }
}