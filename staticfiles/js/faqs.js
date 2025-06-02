document.addEventListener("DOMContentLoaded", () => {
    const faqs = [
        {
            id: "1",
            question: "¿Qué es aprobado?",
            answer: "Aprobado es una fintech colombiana que ofrece microcréditos digitales para emprendedoras y pequeños negocios, sin necesidad de historial crediticio.",
            category: "general"
        },
        {
            id: "2",
            question: "¿Cuáles son los requisitos para solicitar un crédito?",
            answer: "Ser mayor de edad, tener documento de identidad y contar con una\n" +
                "billetera digital o cuenta bancaria activa.",
            category: "general"
        },
        {
            id: "3",
            question: "¿En cuánto tiempo me aprueban el crédito?",
            answer: "En minutos. Nuestro proceso es 100% digital, sin papeleo ni filas.",
            category: "general"
        },
        {
            id: "4",
            question: "¿Qué tipo de financiación ofrecen?",
            answer: "Ofrecemos tres tipos de microcréditos: iniciales, recurrentes y\n" +
                "solidarios, con montos desde $100.000 hasta $3.000.000 COP.",
            category: "financiacion"
        },
        {
            id: "6",
            question: "¿Cómo sé que mi información está segura?",
            answer: "Usamos plataformas digitales seguras y tus datos están protegidos\n" +
                "bajo normas legales de privacidad en Colombia.",
            category: "seguridad"
        },
        {
            id: "7",
            question: "¿Cuánto es el interés del crédito?",
            answer: "Nuestras tasas son del 4.5% mensual, más una cuota única del 10% por\n" +
                "administración. Todo está claro desde el inicio.",
            category: "intereses"
        },
        {
            id: "8",
            question: "¿Qué tasas de interés manejan?",
            answer: "Lorem Ipsum es simplemente el texto de relleno...",
            category: "intereses"
        },
        {
            id: "9",
            question: "¿Qué pasa si no puedo pagar a tiempo?",
            answer: "Ofrecemos acompañamiento y opciones para reestructurar el crédito.\n" +
                "Lo importante es que te comuniques con nosotros.",
            category: "ayudas"
        },
        {
            id: "10",
            question: "¿Aprobado ofrece apoyo adicional además del crédito?",
            answer: "Sí. Te damos orientación básica financiera y un seguimiento cercano\n" +
                "para que tu negocio siga creciendo.",
            category: "ayudas"
        },
    ];

    let currentCategory = "general";
    const grid = document.getElementById("faq-grid");
    const buttons = document.querySelectorAll(".tab-button");

    function renderFaqs() {
        grid.innerHTML = "";
        const filtered = faqs.filter(f => f.category === currentCategory);

        filtered.forEach(faq => {
            const card = document.createElement("div");
            card.className = "faq-card";

            const header = document.createElement("div");
            header.className = "faq-header";

            const question = document.createElement("h3");
            question.textContent = faq.question;

            const toggle = document.createElement("button");
            toggle.className = "toggle-button";
            toggle.innerHTML = "+";

            const answer = document.createElement("div");
            answer.className = "faq-answer";
            answer.style.display = "none";
            answer.textContent = faq.answer;

            toggle.addEventListener("click", () => {
                const isVisible = answer.style.display === "block";
                answer.style.display = isVisible ? "none" : "block";
                toggle.innerHTML = isVisible ? "+" : "−";
            });

            header.appendChild(question);
            header.appendChild(toggle);
            card.appendChild(header);
            card.appendChild(answer);
            grid.appendChild(card);
        });
    }

    buttons.forEach(btn => {
        btn.addEventListener("click", () => {
            buttons.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            currentCategory = btn.dataset.category;
            renderFaqs();
        });
    });

    renderFaqs();
});
