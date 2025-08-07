/*
    Author: Qinbo Li
    Date: 10/17/2017
    Requirement: jquery-3.2.1
    This file defines the interactive behavior of the entire site
*/

window.addEventListener( "pageshow", function ( event ) {
    var historyTraversal = event.persisted || ( typeof window.performance != "undefined" && window.performance.navigation.type === 2 );
    if ( historyTraversal ) {
        // Handle page restore.
        window.location.reload();
    }
});
