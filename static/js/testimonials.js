const testimonials = [
    {
        title: "Fue realmente fácil",
        content: "Lorem Ipsum es simplemente el texto de relleno de las imprentas...",
        author: {name: "Mauricio Jaramillo", role: "Emprendedor", avatar: "/static/images/testimonials/avatar-1.png"},
        rating: 5.0,
    },
    {
        title: "Me asesoró un experto",
        content: "Lorem Ipsum es simplemente el texto de relleno de las imprentas...",
        author: {name: "Marcela Edar", role: "Dueña de negocio", avatar: "/static/images/testimonials/avatar-2.png"},
        rating: 5.0,
    },
    {
        title: "Muy conveniente",
        content: "Lorem Ipsum es simplemente el texto de relleno de las imprentas...",
        author: {name: "Fernando Ruiz", role: "Empresario", avatar: "/static/images/testimonials/avatar-3.png"},
        rating: 5.0,
    },
    {
        title: "Proceso transparente",
        content: "Lorem Ipsum es simplemente el texto de relleno de las imprentas...",
        author: {name: "Carolina Mendez", role: "Emprendedora", avatar: "/static/images/testimonials/avatar-4.png"},
        rating: 5.0,
    },
]

let currentIndex = 0
let visibleCount = 3

const carousel = document.getElementById("testimonialCarousel")
const prevBtn = document.getElementById("prevBtn")
const nextBtn = document.getElementById("nextBtn")

function renderTestimonials() {
    carousel.innerHTML = ""
    testimonials.forEach((t) => {
        const card = document.createElement("div")
        card.className = `testimonial-card`
        card.style.width = `${100 / visibleCount}%`
        card.innerHTML = `
        <h3>${t.title}</h3>
        <p>${t.content}</p>
        <div class="testimonial-footer">
          <div class="testimonial-author">
            <img src="${t.author.avatar}" alt="${t.author.name}" />
            <div class="testimonial-author-info">
              <p><strong>${t.author.name}</strong></p>
              <p class="role">${t.author.role}</p>
            </div>
          </div>
          <div class="rating">
            <svg viewBox="0 0 24 24" fill="currentColor" width="24" height="24">
              <path d="M12 17.27L18.18 21L16.54 13.97L22 9.24L14.81 8.63L12 2L9.19 8.63L2 9.24L7.45 13.97L5.82 21L12 17.27Z" />
              </svg>
            <span>${t.rating.toFixed(1)}</span>
          </div>
        </div>
    `
        carousel.appendChild(card)
    })
    updateCarouselPosition()
}

function updateCarouselPosition() {
    const shift = (currentIndex * 100) / visibleCount
    carousel.style.transform = `translateX(-${shift}%)`
}

function adjustVisibleCount() {
    const width = window.innerWidth
    if (width < 640) visibleCount = 1
    else if (width < 1024) visibleCount = 2
    else visibleCount = 3
    renderTestimonials()
}

prevBtn.addEventListener("click", () => {
    currentIndex = currentIndex <= 0 ? testimonials.length - visibleCount : currentIndex - 1
    updateCarouselPosition()
})

nextBtn.addEventListener("click", () => {
    currentIndex = currentIndex >= testimonials.length - visibleCount ? 0 : currentIndex + 1
    updateCarouselPosition()
})

window.addEventListener("resize", adjustVisibleCount)
window.addEventListener("DOMContentLoaded", () => {
    adjustVisibleCount()
})
