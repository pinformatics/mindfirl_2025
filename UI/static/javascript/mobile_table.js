// function addColumnPager(tableSelector, colsPerPage = 3) {
//     const table = document.querySelector(tableSelector);
//     const rows = table.querySelectorAll("tr");

//     // Get the total number of columns
//     const colCount = rows[0].children.length;
//     const numPages = Math.ceil(colCount / colsPerPage);

//     // Create pager
//     const pagerDiv = document.createElement("div");
//     pagerDiv.className = "column-nav";

//     for (let i = 0; i < numPages; i++) {
//         const a = document.createElement("a");
//         a.href = "#";
//         a.textContent = i + 1;
//         a.dataset.page = i;
//         a.addEventListener("click", e => {
//             e.preventDefault();
//             changeToColumnPage(table, i, colsPerPage);
//         });
//         pagerDiv.appendChild(a);
//     }

//     // Insert pager after table
//     table.parentNode.insertBefore(pagerDiv, table.nextSibling);

//     // Show the first page
//     changeToColumnPage(table, 0, colsPerPage);
// }

// function changeToColumnPage(table, pageIndex, colsPerPage) {
//     const startCol = pageIndex * colsPerPage;
//     const endCol = startCol + colsPerPage;

//     // Update the pair label
//     // const pairLabelEl = document.getElementById("pair-label");
//     // if (pairLabelEl) {
//     //     const totalCols = table.querySelector("tr")?.children.length || 0;
//     //     const totalPages = Math.ceil(totalCols / colsPerPage);
//     //     pairLabelEl.textContent = `Pair ${pageIndex + 1}/${totalPages}`;
//     // }

//     // Go through each row
//     const rows = table.querySelectorAll("tr");
//     for (const row of rows) {
//         const cells = row.children;
//         for (let i = 0; i < cells.length; i++) {
//             cells[i].style.display = (i >= startCol && i < endCol) ? "" : "none";
//         }
//     }

//     // Highlight current page
//     const nav = table.nextSibling;
//     const links = nav.querySelectorAll("a");
//     links.forEach((link, i) => {
//         link.classList.toggle("active", i === pageIndex);
//     });
// }
